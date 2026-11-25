"""Use real UDP on loopback, never a physical device or an external network."""
import socket
import struct
from contextlib import contextmanager

import pytest

from serial_hub_mcp.tftp_server import TftpServer


HOST = '127.0.0.1'


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


@contextmanager
def serving(root):
    server = TftpServer(HOST, free_port(), str(root))
    server.start()
    try:
        yield server
    finally:
        server.stop()


def rrq(name='boot.bin', opts=b''):
    return b'\0\1' + name.encode() + b'\0octet\0' + opts


def client():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    return sock


def download(server, options=False):
    with client() as sock:
        sock.sendto(rrq(opts=b'blksize\x00128\x00tsize\x000\x00' if options else b''), (HOST, server.port))
        packet, tid = sock.recvfrom(2048)
        assert tid[0] == HOST and tid[1] != server.port
        if options:
            assert packet.startswith(b'\0\6blksize\x00128\x00tsize\x00')
            sock.sendto(b'\0\4\0\0', tid)
            packet, peer = sock.recvfrom(2048)
            assert peer == tid
        result = bytearray()
        for number in range(1, 1024):
            assert packet[:4] == struct.pack('!HH', 3, number)
            result.extend(packet[4:])
            sock.sendto(struct.pack('!HH', 4, number), tid)
            if len(packet[4:]) < (128 if options else 512):
                return bytes(result)
            packet, peer = sock.recvfrom(2048)
            assert peer == tid
        pytest.fail('No final DATA block')


@pytest.mark.parametrize('data,options', [(b'', False), (bytes(range(256)) * 4, False),
                                          (bytes(range(256)) * 8 + b'END', True)])
def test_read_real_firmware(tmp_path, data, options):
    (tmp_path / 'boot.bin').write_bytes(data)
    with serving(tmp_path) as server:
        assert server.running and download(server, options) == data
    assert not server.running


def test_no_upload_or_outside_files(tmp_path):
    root = tmp_path / 'firmware'
    root.mkdir()
    (tmp_path / 'private').write_bytes(b'not a firmware image')
    with serving(root) as server, client() as sock:
        for request in [b'\0\2new.bin\0octet\0', rrq('../private'), rrq('CON'), rrq('a:b'), rrq('a\\b')]:
            sock.sendto(request, (HOST, server.port))
            assert sock.recv(1024)[:4] == b'\0\5\0\2'
        assert not list(root.iterdir())


def test_reject_file_link(tmp_path):
    root = tmp_path / 'serve'
    root.mkdir()
    private = tmp_path / 'private'
    private.write_bytes(b'separate')
    try:
        (root / 'boot.bin').symlink_to(private)
    except OSError:
        pytest.skip('Symlink creation unavailable')
    with serving(root) as server, client() as sock:
        sock.sendto(rrq(), (HOST, server.port))
        assert sock.recv(1024)[:4] == b'\0\5\0\2'


def test_two_instances_conflict_and_stop_active_transfer(tmp_path):
    roots = [tmp_path / s for s in ('a', 'b')]
    for i, root in enumerate(roots):
        root.mkdir()
        (root / 'boot.bin').write_bytes(bytes([i + 1]) * 1025)
    with serving(roots[0]) as a, serving(roots[1]) as b:
        other = TftpServer(HOST, a.port, str(roots[1]))
        with pytest.raises(OSError):
            other.start()
        assert not other.running and a.running
        assert download(a) != download(b)
        with client() as sock:
            sock.sendto(rrq(), (HOST, a.port))
            packet, tid = sock.recvfrom(1024)
            assert packet[:4] == b'\0\3\0\1'
            a.stop()
            a.stop()
            assert not a.running and b.running
            # The paused transfer TID, not just the listening port, is released.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(tid)
        other.start()
        try:
            assert download(other) == download(b)
        finally:
            other.stop()


@pytest.mark.parametrize('ip', ['0.0.0.0', '::', '255.255.255.255', '224.0.0.1', 'localhost'])
def test_explicit_unicast_only(tmp_path, ip):
    with pytest.raises(ValueError):
        TftpServer(ip, free_port(), str(tmp_path)).start()


def test_retries_missing_ack_then_finishes(tmp_path, monkeypatch):
    from serial_hub_mcp import tftp_server
    monkeypatch.setattr(tftp_server, '_RETRY', .05)
    (tmp_path / 'boot.bin').write_bytes(b'b' * 512)
    with serving(tmp_path) as server, client() as sock:
        sock.sendto(rrq(), (HOST, server.port))
        first, tid = sock.recvfrom(1024)
        assert sock.recvfrom(1024) == (first, tid)
        sock.sendto(b'\0\4\0\1', tid)
        packet, _ = sock.recvfrom(1024)
        assert packet == b'\0\3\0\2'
        sock.sendto(b'\0\4\0\2', tid)
