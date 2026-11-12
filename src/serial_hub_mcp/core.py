from __future__ import annotations

import asyncio
import base64
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path
import re
import time
import uuid

import serial
from serial.tools import list_ports


class HubError(Exception):
    pass


def port_name(value: str) -> str:
    value = value.upper().strip()
    if not re.fullmatch(r"COM[1-9][0-9]{0,4}", value) or int(value[3:]) > 65535:
        raise HubError("Use a Windows COM name, for example COM3 (COM1..COM65535).")
    return value


@dataclass(frozen=True)
class PortConfig:
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    flow_control: str = "none"
    dtr: bool = False
    rts: bool = False

    def validate(self) -> None:
        if not 50 <= self.baudrate <= 4_000_000:
            raise HubError("baudrate must be 50..4000000")
        if self.bytesize not in (5, 6, 7, 8) or self.parity not in "NEOMS" or len(self.parity) != 1:
            raise HubError("Invalid bytesize or parity")
        if self.stopbits not in (1, 1.5, 2) or self.flow_control not in ("none", "rtscts", "xonxoff"):
            raise HubError("Invalid stopbits or flow_control")


class Journal:
    """Per-port RX/TX/event JSONL, bounded on disk; never goes to stdout."""

    def __init__(self, directory: Path, name: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.Logger(f"serial-hub.{name}.{uuid.uuid4().hex}")
        self.logger.propagate = False
        self.handler = RotatingFileHandler(
            directory / f"{name}.jsonl", maxBytes=8 * 1024 * 1024,
            backupCount=4, encoding="utf-8",
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)
        self.path = str(directory / f"{name}.jsonl")

    def record(self, kind: str, data: bytes = b"", **fields) -> None:
        self.logger.warning(json.dumps({
            "time": datetime.now(timezone.utc).isoformat(), "kind": kind,
            "base64": base64.b64encode(data).decode("ascii"), **fields,
        }, ensure_ascii=True))

    def close(self) -> None:
        self.handler.close()


class SerialPort:
    def __init__(self, name: str, config: PortConfig, device, journal: Journal, capacity: int):
        self.name, self.config, self.device, self.journal = name, config, device, journal
        self.epoch = uuid.uuid4().hex
        self.capacity = capacity
        self.buffer = bytearray()
        self.start = self.end = 0
        self.owner: str | None = None
        self.deadline = 0.0
        self.generation = 0
        self.uncertain = False
        self.error: str | None = None
        self.connected = True
        self.telnet_port: int | None = None
        self.clients: set[str] = set()
        self.condition = asyncio.Condition()
        self.queue: deque[object] = deque()
        self.operation = asyncio.Lock()
        self.reader = asyncio.create_task(self._reader())
        journal.record("open", config=asdict(config), epoch=self.epoch)

    async def _reader(self) -> None:
        try:
            while self.connected:
                data = await asyncio.to_thread(self.device.read, 4096)
                if data:
                    async with self.condition:
                        self.buffer.extend(data)
                        self.end += len(data)
                        if len(self.buffer) > self.capacity:
                            del self.buffer[:len(self.buffer) - self.capacity]
                        self.start = self.end - len(self.buffer)
                        self.journal.record("rx", data, end=self.end)
                        self.condition.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.condition:
                self.connected = False
                self.error = str(exc)
                self.uncertain = True
                self.generation += 1
                self.journal.record("disconnect", error=str(exc))
                self.condition.notify_all()

    def _expire(self) -> None:
        if self.owner and time.monotonic() >= self.deadline:
            previous = self.owner
            self.owner = None
            self.generation += 1
            self.journal.record("control_expired", owner=previous, uncertain=self.uncertain)

    def cursor(self, offset: int) -> str:
        return f"{self.epoch}:{offset}"

    def offset(self, cursor: str | None) -> int:
        if cursor is None:
            return self.start
        try:
            epoch, value = cursor.split(":", 1)
            offset = int(value)
        except (ValueError, AttributeError):
            raise HubError("Invalid cursor") from None
        if epoch != self.epoch:
            raise HubError("Cursor belongs to an earlier port opening; read without cursor to resynchronize.")
        if not 0 <= offset <= self.end:
            raise HubError("Cursor is outside this stream")
        return offset

    def snapshot(self) -> dict:
        self._expire()
        return {
            "port": self.name, "config": asdict(self.config), "connected": self.connected,
            "error": self.error, "owner": self.owner,
            "lease_seconds_remaining": max(0, round(self.deadline - time.monotonic(), 1)) if self.owner else 0,
            "needs_confirmation": self.uncertain, "queued": len(self.queue),
            "cursor_start": self.cursor(self.start), "cursor_end": self.cursor(self.end),
            "telnet": f"127.0.0.1:{self.telnet_port}" if self.telnet_port else None,
            "terminal_clients": len(self.clients), "log_path": self.journal.path,
        }

    async def read(self, cursor: str | None = None, max_bytes: int = 16384,
                   wait_seconds: float = 0, encoding: str = "utf-8") -> dict:
        if not 1 <= max_bytes <= 65536 or not 0 <= wait_seconds <= 30:
            raise HubError("max_bytes must be 1..65536, wait_seconds 0..30")
        try:
            b"".decode(encoding)
        except LookupError:
            raise HubError("Unknown encoding") from None
        offset = self.offset(cursor)
        async with self.condition:
            if offset == self.end and self.connected and wait_seconds:
                try:
                    await asyncio.wait_for(self.condition.wait(), wait_seconds)
                except TimeoutError:
                    pass
            lost = max(0, self.start - offset)
            offset = max(offset, self.start)
            data = bytes(self.buffer[offset - self.start:offset - self.start + max_bytes])
            return {
                "port": self.name, "text": data.decode(encoding, errors="replace"),
                "base64": base64.b64encode(data).decode("ascii"),
                "cursor": self.cursor(offset + len(data)), "bytes_lost": lost,
                "has_more": offset + len(data) < self.end,
                "connected": self.connected, "error": self.error,
            }

    async def acquire(self, owner: str, lease_seconds: float = 120,
                      wait_seconds: float = 0, force: bool = False) -> dict:
        if not owner or not 1 <= lease_seconds <= 3600 or not 0 <= wait_seconds <= 60:
            raise HubError("lease_seconds must be 1..3600, wait_seconds 0..60")
        ticket = object()
        deadline = time.monotonic() + wait_seconds
        async with self.condition:
            self.queue.append(ticket)
            try:
                while True:
                    self._expire()
                    if not self.connected:
                        raise HubError("Serial port is disconnected")
                    if force:
                        # Reserved for the human Telnet path, never exposed in MCP/API.
                        if self.owner != owner:
                            self.uncertain = self.uncertain or self.owner is not None
                            self.generation += 1
                        self.owner, self.deadline = owner, time.monotonic() + lease_seconds
                        self.journal.record("human_takeover", owner=owner)
                        self.condition.notify_all()
                        return self.snapshot()
                    if self.owner == owner:
                        self.deadline = time.monotonic() + lease_seconds
                        return self.snapshot()
                    if not self.owner and self.uncertain:
                        raise HubError("Terminal state is unknown. Inspect output and call acknowledge_state to confirm idle; do not blindly send another command.")
                    if not self.owner and self.queue[0] is ticket:
                        self.owner, self.deadline = owner, time.monotonic() + lease_seconds
                        self.generation += 1
                        self.journal.record("control_acquired", owner=owner)
                        return self.snapshot()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HubError(f"Port is controlled by {self.owner or 'a queued caller'}")
                    try:
                        await asyncio.wait_for(self.condition.wait(), min(remaining, 0.25))
                    except TimeoutError:
                        pass
            finally:
                self.queue.remove(ticket)
                self.condition.notify_all()

    def require_owner(self, owner: str) -> None:
        self._expire()
        if not self.connected:
            raise HubError("Serial port is disconnected")
        if self.owner != owner:
            raise HubError("You do not hold this port's control lease")

    async def release(self, owner: str, confirmed_idle: bool = False) -> dict:
        async with self.condition:
            self.require_owner(owner)
            if self.operation.locked():
                raise HubError("An operation is still running")
            if confirmed_idle:
                self.uncertain = False
            self.owner = None
            self.generation += 1
            self.journal.record("control_released", owner=owner, uncertain=self.uncertain)
            self.condition.notify_all()
            return self.snapshot()

    async def acknowledge(self, owner: str) -> dict:
        async with self.condition:
            self._expire()
            if self.owner not in (None, owner) or self.operation.locked():
                raise HubError("Another controller/operation is active")
            self.uncertain = False
            self.journal.record("state_confirmed", owner=owner)
            self.condition.notify_all()
            return self.snapshot()

    async def _write(self, owner: str, data: bytes) -> None:
        self.require_owner(owner)
        self.uncertain = True
        self.journal.record("tx", data, owner=owner)
        try:
            # Serial writes are deliberately synchronous and bounded (1s timeout).
            # A takeover cannot race a queued worker thread that writes stale input.
            count = self.device.write(data)
            if count != len(data):
                raise HubError("Partial serial write; terminal state is unknown")
        except Exception as exc:
            raise HubError(f"Serial write failed; do not retry blindly: {exc}") from exc

    async def send(self, owner: str, data: bytes) -> dict:
        if not 1 <= len(data) <= 65536:
            raise HubError("Send 1..65536 bytes")
        if self.operation.locked():
            raise HubError("An operation is still running on this port")
        async with self.operation:
            await self._write(owner, data)
            return {"written": len(data), **self.snapshot()}

    async def execute(self, owner: str, text: str, expect: str, timeout: float = 10,
                      wait_seconds: float = 30, newline: str = "\r",
                      encoding: str = "utf-8") -> dict:
        if not expect or len(expect) > 2048 or not 0.1 <= timeout <= 120:
            raise HubError("Provide a literal expect string (1..2048 chars) and timeout 0.1..120s")
        try:
            data = (text + newline).encode(encoding)
            expected = expect.encode(encoding)
        except (LookupError, UnicodeError) as exc:
            raise HubError(str(exc)) from exc
        if not 1 <= len(data) <= 65536:
            raise HubError("Command exceeds 65536 bytes")
        had_control = self.owner == owner and time.monotonic() < self.deadline
        await self.acquire(owner, lease_seconds=max(120, timeout + 10), wait_seconds=wait_seconds)
        if self.operation.locked():
            raise HubError("An operation is already running for this controller")
        output = bytearray()
        lost = 0
        outcome = "timeout"
        async with self.operation:
            if self.uncertain:
                raise HubError("Confirm terminal idle with acknowledge_state before execute")
            generation = self.generation
            cursor = self.cursor(self.end)
            try:
                await self._write(owner, data)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self.owner != owner or self.generation != generation:
                        outcome = "interrupted"
                        break
                    result = await self.read(cursor, max_bytes=65536,
                                             wait_seconds=min(0.2, max(0, deadline - time.monotonic())))
                    cursor = result["cursor"]
                    lost += result["bytes_lost"]
                    output.extend(base64.b64decode(result["base64"]))
                    if self.owner != owner or self.generation != generation:
                        outcome = "interrupted"
                        break
                    # Check before truncation so a long continuous output is bounded.
                    if expected in output:
                        outcome = "matched"
                        break
                    if len(output) > 65536:
                        lost += len(output) - 65536
                        del output[:-65536]
                    if not result["connected"]:
                        outcome = "disconnected"
                        break
                if outcome == "matched" and self.generation == generation and self.owner == owner:
                    self.uncertain = False
            finally:
                # Any error/cancellation leaves the write marked uncertain.
                # Never release a human's newer lease after forced takeover.
                if not had_control and self.owner == owner and self.generation == generation:
                    async with self.condition:
                        self.owner = None
                        self.generation += 1
                        self.condition.notify_all()
        if len(output) > 65536:
            lost += len(output) - 65536
            del output[:-65536]
        return {"outcome": outcome, "text": output.decode(encoding, errors="replace"),
                "base64": base64.b64encode(output).decode("ascii"), "cursor": cursor,
                "bytes_lost": lost, "needs_confirmation": self.uncertain}

    async def close(self) -> None:
        self.connected = False
        self.generation += 1
        # Let the bounded serial read finish before closing its handle; cancelling
        # to_thread does not stop the underlying thread.
        try:
            await asyncio.wait_for(asyncio.shield(self.reader), timeout=1.5)
        except (TimeoutError, asyncio.CancelledError):
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await asyncio.to_thread(self.device.close)
        self.journal.record("close")
        self.journal.close()
        async with self.condition:
            self.condition.notify_all()


class Hub:
    def __init__(self, log_directory: Path, factory=None, capacity: int = 1024 * 1024):
        self.log_directory, self.factory, self.capacity = log_directory, factory or self._serial, capacity
        self.ports: dict[str, SerialPort] = {}
        self.lifecycle = asyncio.Lock()
        self.terminals = None

    @staticmethod
    def _serial(name: str, config: PortConfig):
        device = serial.Serial(port=None, baudrate=config.baudrate, bytesize=config.bytesize,
                               parity=config.parity, stopbits=config.stopbits, timeout=0.1,
                               write_timeout=1, rtscts=config.flow_control == "rtscts",
                               xonxoff=config.flow_control == "xonxoff")
        device.dtr, device.rts = config.dtr, config.rts
        device.port = name
        device.open()
        return device

    def get(self, name: str) -> SerialPort:
        name = port_name(name)
        if name not in self.ports:
            raise HubError(f"{name} is not open. Call open_port first.")
        return self.ports[name]

    def inventory(self) -> dict:
        return {"available": [{"port": p.device, "description": p.description,
                               "hwid": p.hwid} for p in list_ports.comports()],
                "open": [p.snapshot() for p in self.ports.values()]}

    async def open(self, name: str, config: PortConfig) -> dict:
        name = port_name(name)
        config.validate()
        async with self.lifecycle:
            if name in self.ports:
                port = self.ports[name]
                if port.config != config:
                    raise HubError("Port is already open with different parameters; no settings were changed.")
                return port.snapshot()
            if len(self.ports) >= 32:
                raise HubError("At most 32 ports may be open")
            try:
                device = await asyncio.to_thread(self.factory, name, config)
            except Exception as exc:
                raise HubError(f"Cannot open {name}; another program may own it: {exc}") from exc
            try:
                journal = Journal(self.log_directory, name)
            except Exception:
                device.close()
                raise
            port = SerialPort(name, config, device, journal, self.capacity)
            self.ports[name] = port
            try:
                if self.terminals:
                    port.telnet_port = await self.terminals.listen(port)
            except Exception:
                del self.ports[name]
                await port.close()
                raise
            return port.snapshot()

    async def close_port(self, name: str, owner: str) -> dict:
        async with self.lifecycle:
            port = self.get(name)
            port._expire()
            if port.owner not in (None, owner) or port.clients or port.queue or port.operation.locked():
                raise HubError("Port has another controller, terminal clients, queued calls or an active operation")
            if self.terminals:
                await self.terminals.stop_port(port.name)
            await port.close()
            del self.ports[port.name]
            return {"closed": port.name}

    async def shutdown(self) -> None:
        if self.terminals:
            await self.terminals.close()
        for port in list(self.ports.values()):
            await port.close()
        self.ports.clear()
