from __future__ import annotations

import asyncio
import base64
import codecs
import secrets
import uuid

import telnetlib3

from .core import HubError, SerialPort


async def read_line(reader, limit: int = 256) -> str:
    result = ""
    while True:
        char = await reader.read(1)
        if not char:
            raise EOFError
        if char in "\r\n":
            return result
        if char == "\x00":
            continue
        if char in ("\x08", "\x7f"):
            result = result[:-1]
        else:
            result += char
        if len(result) > limit:
            raise HubError("Input line too long")


class Terminals:
    def __init__(self, token: str, base_port: int = 2300, encoding: str = "utf-8"):
        self.token, self.base_port, self.encoding = token, base_port, encoding
        codecs.lookup(encoding)
        if not 1024 <= base_port <= 64000:
            raise ValueError("Telnet base port must be 1024..64000")
        self.servers = {}
        self.tasks: set[asyncio.Task] = set()
        self.connections: set = set()

    async def listen(self, port: SerialPort) -> int:
        preferred = self.base_port + int(port.name[3:])
        candidates = [preferred, 0] if preferred <= 65535 else [0]
        for number in candidates:
            try:
                server = await telnetlib3.create_server(
                    host="127.0.0.1", port=number, shell=lambda r, w: self.shell(port, r, w),
                    encoding=self.encoding, encoding_errors="replace", connect_maxwait=1,
                    timeout=0, limit=65536,
                )
                self.servers[port.name] = server
                return server.sockets[0].getsockname()[1]
            except OSError:
                if number == 0:
                    raise
        raise RuntimeError("Unable to create terminal listener")

    async def shell(self, port: SerialPort, reader, writer) -> None:
        if len(self.tasks) >= 128:
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        owner = "human:" + uuid.uuid4().hex[:12]
        pump = None
        authenticated = False
        try:
            writer.write(f"\r\nSerial Hub - {port.name}\r\nAccess token: ")
            await writer.drain()
            supplied = await asyncio.wait_for(read_line(reader), timeout=30)
            if not secrets.compare_digest(supplied.encode("utf-8"), self.token.encode("ascii")):
                writer.write("\r\nAuthentication failed.\r\n")
                await writer.drain()
                return
            if len(port.clients) >= 16:
                writer.write("\r\nToo many terminal clients.\r\n")
                await writer.drain()
                return
            authenticated = True
            port.clients.add(owner)
            writer.write(f"\r\n[hub] Connected to {port.name} in SHARED READ/WRITE mode.\r\n"
                         "[hub] All clients can type immediately. Input/output may mix; no takeover keys.\r\n")
            await writer.drain()
            pump = asyncio.create_task(self._output(port, writer))
            # A failed/slow output consumer must also disconnect its input side.
            pump.add_done_callback(lambda _: writer.close())
            after_cr = True  # swallow optional LF/NUL left from token CRLF
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                text = []
                for char in chunk:
                    # Telnet NVT CR LF / CR NUL represent one Enter. Preserve
                    # standalone NUL and all control keys (including Ctrl+]).
                    if after_cr and char in ("\n", "\x00"):
                        after_cr = False
                        continue
                    text.append(char)
                    after_cr = char == "\r"
                if text:
                    try:
                        await port.send(owner, "".join(text).encode(self.encoding))
                    except HubError as exc:
                        writer.write(f"\r\n[hub] {exc}\r\n")
                        await writer.drain()
        except (EOFError, TimeoutError, ConnectionError, OSError):
            pass
        finally:
            if pump:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            if authenticated:
                port.clients.discard(owner)
            writer.close()
            self.connections.discard(writer)
            self.tasks.discard(task)

    async def _output(self, port: SerialPort, writer) -> None:
        cursor = port.cursor(max(port.start, port.end - 4096))
        decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")
        while True:
            result = await port.read(cursor, max_bytes=16384, wait_seconds=1)
            cursor = result["cursor"]
            if result["bytes_lost"]:
                decoder.reset()
                writer.write(f"\r\n[hub] Lost {result['bytes_lost']} buffered bytes.\r\n")
            data = base64.b64decode(result["base64"])
            if data:
                writer.write(decoder.decode(data))
                # A stalled viewer must not block serial RX or other viewers.
                await asyncio.wait_for(writer.drain(), timeout=5)
            if not result["connected"] and not result["has_more"]:
                writer.write(f"\r\n[hub] Serial disconnected: {result['error']}\r\n")
                await writer.drain()
                writer.close()
                return

    async def stop_port(self, name: str) -> None:
        server = self.servers.pop(name, None)
        if server:
            server.close()
            await server.wait_closed()

    async def close(self) -> None:
        for server in self.servers.values():
            server.close()
        for server in self.servers.values():
            await server.wait_closed()
        self.servers.clear()
        for writer in list(self.connections):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
