"""Manually started, read-only TFTP for a trusted device network.

Serve ordinary files directly inside a dedicated local directory, in octet mode.
RFC 1350 plus optional blksize (8..1468) and tsize. Large transfers roll block
numbers from 65535 to 0. No authentication, directory listing, uploads or boot
commands. A server owns one listener and at most eight ephemeral transfer TIDs.
"""
from __future__ import annotations

import ipaddress
import os
import selectors
import socket
import stat
import struct
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import BinaryIO


_RETRY = 1.5
_RETRIES = 4
_MAX_TRANSFERS = 8
_MAX_DURATION = 3600


def _error(sock, peer, code, message):
    # Never send paths or local OS diagnostics to clients.
    try:
        sock.sendto(struct.pack('!HH', 5, code) + message + b'\0', peer)
    except OSError:
        pass


def _reparse(st):
    return bool(getattr(st, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _filename(raw):
    name = raw.decode('utf-8')
    if (not name or len(raw) > 255 or not name.isprintable() or name.startswith('.')
            or name.endswith(('.', ' ')) or any(ch in name for ch in '/\\:<>"|?*')
            or (os.path.isreserved(name) if hasattr(os.path, 'isreserved') else PureWindowsPath(name).is_reserved())):
        raise PermissionError
    return name


def _open_file(root, root_stat, name):
    # The local operator controls the directory; do not permit links to other
    # content. Check the opened handle against lstat before returning any bytes.
    path = root / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or _reparse(info) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_HIDDEN):
        raise PermissionError
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    stream = os.fdopen(os.open(path, flags), 'rb')
    try:
        opened = os.fstat(stream.fileno())
        if (not os.path.samestat(info, opened) or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1 or not os.path.samestat(root_stat, root.lstat())):
            raise PermissionError
        return stream, opened.st_size
    except BaseException:
        stream.close()
        raise


def _options(fields, size):
    options = {}
    for k, v in zip(fields[::2], fields[1::2]):
        k = k.lower()
        if not k or not v:
            raise ValueError
        if k in (b'blksize', b'tsize'):
            if k in options or not v.isdigit() or len(v) > 8:
                raise ValueError
            n = int(v)
            if k == b'blksize' and 8 <= n <= 65464:
                options[k] = min(n, 1468)
            elif k == b'tsize' and n == 0:
                options[k] = size
            else:
                raise ValueError
    return options


@dataclass
class _Transfer:
    peer: tuple
    sock: socket.socket
    file: BinaryIO
    blocksize: int
    expires: float
    block: int = 0
    packet: bytes = b''
    due: float = 0
    retries: int = 0
    final: bool = False

    def next_data(self):
        data = self.file.read(self.blocksize)
        self.block = (self.block + 1) & 0xffff
        self.packet = struct.pack('!HH', 3, self.block) + data
        self.final = len(data) < self.blocksize
        self.retries = 0

    def send(self):
        self.sock.sendto(self.packet, self.peer)
        self.due = time.monotonic() + _RETRY


class TftpServer:
    """Instantiate without I/O; start/stop only from the GUI's lifecycle thread."""
    def __init__(self, ip: str, port: int, directory: str):
        self.ip, self.port, self.directory = ip, port, directory
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self):
        if self.running:
            return
        ip = ipaddress.ip_address(self.ip)
        if (ip.is_unspecified or ip.is_multicast or str(ip) == '255.255.255.255'
                or (ip.version == 4 and (int(ip) >> 24) == 0)):
            raise ValueError('请选择具体的本机单播 IP，不要使用全部接口、组播或广播地址。')
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError('TFTP UDP 端口必须在 1 至 65535 之间。')
        root = Path(self.directory)
        if not root.is_absolute() or (os.name == 'nt' and root.drive.startswith('\\\\')):
            raise ValueError('请选择专用的本地固件目录。')
        info = root.lstat()
        root = root.resolve(strict=True)
        if root == root.parent or not stat.S_ISDIR(info.st_mode) or _reparse(info):
            raise ValueError('请选择普通固件子目录，不要使用磁盘根目录或链接。')
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind((self.ip, self.port))
            listener.setblocking(False)
            self._stop.clear()
            self._thread = threading.Thread(target=self._serve, args=(listener, root, info),
                                            name=f'TFTP {self.ip}:{self.port}', daemon=True)
            self._thread.start()
        except BaseException:
            self._thread = None
            listener.close()
            raise

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _request(self, listener, peer, packet, root, root_stat, transfers, sel):
        opcode = packet[:2]
        if opcode != b'\0\1':
            if opcode == b'\0\2':
                _error(listener, peer, 2, b'Read-only server')
            return
        if peer in transfers:  # Duplicate RRQ: the existing TID retries its last packet.
            return
        if len(transfers) >= _MAX_TRANSFERS:
            _error(listener, peer, 0, b'Server busy')
            return
        fields = packet[2:-1].split(b'\0')
        if (len(packet) > 512 or not packet.endswith(b'\0') or len(fields) < 2
                or len(fields) % 2 or fields[1].lower() != b'octet'):
            _error(listener, peer, 4, b'Expected octet read request')
            return
        try:
            with ExitStack() as stack:
                stream, size = _open_file(root, root_stat, _filename(fields[0]))
                stack.callback(stream.close)
                options = _options(fields[2:], size)
                sock = stack.enter_context(socket.socket(listener.family, socket.SOCK_DGRAM))
                sock.bind((self.ip, 0))
                sock.setblocking(False)
                t = _Transfer(peer, sock, stream, options.get(b'blksize', 512), time.monotonic() + _MAX_DURATION)
                if options:
                    t.packet = b'\0\6' + b''.join(k + b'\0' + str(v).encode('ascii') + b'\0' for k, v in options.items())
                else:
                    t.next_data()
                t.send()
                sel.register(sock, selectors.EVENT_READ, t)
                transfers[peer] = t
                stack.pop_all()
        except FileNotFoundError:
            _error(listener, peer, 1, b'File not found')
        except (OSError, UnicodeError):
            _error(listener, peer, 2, b'Cannot serve file')
        except ValueError:
            _error(listener, peer, 8, b'Invalid option')

    def _serve(self, listener, root, info):
        with listener, selectors.DefaultSelector() as sel:
            transfers = {}
            sel.register(listener, selectors.EVENT_READ)

            def finish(t):
                sel.unregister(t.sock)
                t.sock.close()
                t.file.close()
                del transfers[t.peer]

            try:
                while not self._stop.is_set():
                    for key, _ in sel.select(timeout=.1):
                        t = key.data
                        try:
                            packet, peer = key.fileobj.recvfrom(2048)
                        except (BlockingIOError, ConnectionResetError, ConnectionRefusedError):
                            continue
                        except OSError as exc:
                            if getattr(exc, 'winerror', None) == 10040:  # Oversize UDP datagram.
                                continue
                            raise
                        if t is None:
                            self._request(listener, peer, packet, root, info, transfers, sel)
                        elif peer != t.peer:
                            _error(t.sock, peer, 5, b'Unknown transfer ID')
                        elif packet[:2] == b'\0\5':
                            finish(t)
                        elif packet == struct.pack('!HH', 4, t.block):
                            if t.final:
                                finish(t)
                            else:
                                try:
                                    t.next_data()
                                    t.send()
                                except OSError:
                                    finish(t)
                    now = time.monotonic()
                    for t in list(transfers.values()):
                        if now >= t.expires or (now >= t.due and t.retries >= _RETRIES):
                            finish(t)
                        elif now >= t.due:
                            try:
                                t.retries += 1
                                t.send()
                            except OSError:
                                finish(t)
            finally:
                for t in list(transfers.values()):
                    finish(t)
