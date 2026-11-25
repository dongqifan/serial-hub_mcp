from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import partial
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


class JournalHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        raise


class Journal:
    def __init__(self, directory: Path, name: str, queue_size: int = 256):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = str(directory / f"{name}.jsonl")
        self.epoch = uuid.uuid4().hex
        self.handler = JournalHandler(self.path, maxBytes=8 * 1024 * 1024, backupCount=4, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.pending: asyncio.Queue[tuple | None] = asyncio.Queue(maxsize=queue_size)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name}-journal")
        self.sequence = 0
        self.dropped = 0
        self.error: str | None = None
        self.worker = asyncio.create_task(self._run())

    def _event(self, kind: str, data: bytes, fields: dict) -> tuple:
        self.sequence += 1
        return self.sequence, datetime.now(timezone.utc).isoformat(), kind, data, fields

    def record(self, kind: str, data: bytes = b"", **fields) -> None:
        event = self._event(kind, data, fields)
        try:
            self.pending.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1

    def _emit(self, timestamp: str, kind: str, data: bytes, **fields) -> None:
        message = json.dumps({"time": timestamp, "kind": kind,
                              "base64": base64.b64encode(data).decode("ascii"), **fields}, ensure_ascii=True)
        self.handler.handle(logging.LogRecord("serial-hub", logging.INFO, "", 0, message, (), None))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last = 0
        while (event := await self.pending.get()) is not None:
            sequence, timestamp, kind, data, fields = event
            try:
                if sequence > last + 1:
                    await loop.run_in_executor(self.executor, partial(
                        self._emit, timestamp, "journal_gap", b"", epoch=self.epoch,
                        first_sequence=last + 1, events_lost=sequence - last - 1,
                    ))
                    last = sequence - 1
                await loop.run_in_executor(self.executor, partial(
                    self._emit, timestamp, kind, data, epoch=self.epoch, journal_sequence=sequence, **fields,
                ))
                last = sequence
            except OSError as exc:
                self.dropped += 1
                self.error = str(exc)
            finally:
                self.pending.task_done()
        self.pending.task_done()

    async def close(self) -> None:
        await self.pending.put(self._event("close", b"", {}))
        await self.pending.put(None)
        await self.worker
        await asyncio.get_running_loop().run_in_executor(self.executor, self.handler.close)
        self.executor.shutdown(wait=False)


@dataclass
class WriteRequest:
    owner: str
    data: bytes
    result: asyncio.Future[tuple[dict, str]]


class SerialPort:
    def __init__(self, name: str, config: PortConfig, device, journal: Journal, capacity: int):
        self.name, self.config, self.device, self.journal = name, config, device, journal
        self.epoch = journal.epoch
        self.capacity = capacity
        self.buffer = bytearray()
        self.start = self.end = 0
        self.write_sequence = 0
        self.active_executions = 0
        self.error: str | None = None
        self.connected = True
        self.telnet_port: int | None = None
        self.clients: set[str] = set()
        self.condition = asyncio.Condition()
        self.writes: asyncio.Queue[WriteRequest | None] = asyncio.Queue(maxsize=64)
        self.writing = False
        self.reader_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name}-rx")
        self.writer_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name}-tx")
        journal.record("open", config=asdict(config))
        self.reader = asyncio.create_task(self._reader())
        self.writer = asyncio.create_task(self._writer())
        self.closing: asyncio.Task[None] | None = None

    async def _reader(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self.connected:
                data = await loop.run_in_executor(self.reader_pool, self.device.read, 4096)
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
                self.journal.record("disconnect", error=str(exc))
                self.condition.notify_all()

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
        return {
            "port": self.name, "config": asdict(self.config), "connected": self.connected,
            "error": self.error, "input_mode": "shared",
            "active_executions": self.active_executions, "write_sequence": self.write_sequence,
            "cursor_start": self.cursor(self.start), "cursor_end": self.cursor(self.end),
            "telnet": f"127.0.0.1:{self.telnet_port}" if self.telnet_port else None,
            "terminal_clients": len(self.clients), "log_path": self.journal.path,
            "pending_writes": self.writes.qsize(), "write_in_progress": self.writing,
            "journal_dropped_events": self.journal.dropped, "journal_error": self.journal.error,
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

    async def _writer(self) -> None:
        loop = asyncio.get_running_loop()
        while (request := await self.writes.get()) is not None:
            if request.result.cancelled():
                continue
            if not self.connected:
                request.result.set_exception(HubError("Serial port is disconnected; queued bytes were not sent"))
                continue
            self.writing = True
            cursor = self.cursor(self.end)
            self.write_sequence += 1
            self.journal.record("tx", request.data, source=request.owner, sequence=self.write_sequence)
            error = None
            try:
                count = await loop.run_in_executor(self.writer_pool, self.device.write, request.data)
                if count != len(request.data):
                    raise HubError("Partial serial write; terminal state is unknown")
            except Exception as exc:
                error = exc
                self.journal.record("tx_error", sequence=self.write_sequence, error=str(exc))
            finally:
                self.writing = False
            if request.result.cancelled():
                continue
            if error is not None:
                request.result.set_exception(HubError(f"Serial write failed; do not retry blindly: {error}"))
            else:
                request.result.set_result(({"written": count, **self.snapshot()}, cursor))

    async def _send(self, owner: str, data: bytes) -> tuple[dict, str]:
        if not 1 <= len(data) <= 65536:
            raise HubError("Send 1..65536 bytes")
        if not self.connected:
            raise HubError("Serial port is disconnected")
        result = asyncio.get_running_loop().create_future()
        try:
            self.writes.put_nowait(WriteRequest(owner, data, result))
        except asyncio.QueueFull:
            raise HubError("Serial write queue is full; bytes were not sent") from None
        return await result

    async def send(self, owner: str, data: bytes) -> dict:
        result, _ = await self._send(owner, data)
        return result

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
        output = bytearray()
        lost = 0
        outcome = "timeout"
        self.active_executions += 1
        try:
            sent, cursor = await self._send(owner, data)
            sequence = sent["write_sequence"]
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = await self.read(cursor, max_bytes=65536,
                                         wait_seconds=min(0.2, max(0, deadline - time.monotonic())))
                cursor = result["cursor"]
                lost += result["bytes_lost"]
                output.extend(base64.b64decode(result["base64"]))
                # Every execution observes the same RX stream with its own cursor.
                # A match can be caused by someone else's command, not just ours.
                if expected in output:
                    outcome = "matched"
                    break
                if len(output) > 65536:
                    lost += len(output) - 65536
                    del output[:-65536]
                if not result["connected"]:
                    outcome = "disconnected"
                    break
        finally:
            self.active_executions -= 1
        if len(output) > 65536:
            lost += len(output) - 65536
            del output[:-65536]
        return {"outcome": outcome, "text": output.decode(encoding, errors="replace"),
                "base64": base64.b64encode(output).decode("ascii"), "cursor": cursor,
                "bytes_lost": lost, "input_mode": "shared", "shared_output": True,
                "additional_writes_observed": self.write_sequence - sequence,
                "warning": "Shared terminal output; a match is not proof this command finished or succeeded."}

    async def close(self) -> None:
        if self.closing is None:
            self.connected = False
            self.closing = asyncio.create_task(self._close())
        await asyncio.shield(self.closing)

    async def _close(self) -> None:
        async with self.condition:
            self.condition.notify_all()
        await self.writes.put(None)
        # Wait for both driver calls to finish before releasing the handle.
        await asyncio.gather(self.reader, self.writer)
        try:
            await asyncio.to_thread(self.device.close)
        finally:
            self.reader_pool.shutdown(wait=False)
            self.writer_pool.shutdown(wait=False)
            await self.journal.close()


class Hub:
    def __init__(self, log_directory: Path, factory=None, capacity: int = 1024 * 1024):
        self.log_directory, self.factory, self.capacity = log_directory, factory or self._serial, capacity
        self.ports: dict[str, SerialPort] = {}
        self.lifecycle = asyncio.Lock()
        self.stopping = False
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

    async def inventory(self) -> dict:
        available = await asyncio.to_thread(list_ports.comports)
        return {"available": [{"port": p.device, "description": p.description,
                               "hwid": p.hwid} for p in available],
                "open": [p.snapshot() for p in self.ports.values()]}

    async def open(self, name: str, config: PortConfig) -> dict:
        name = port_name(name)
        config.validate()
        async with self.lifecycle:
            if self.stopping:
                raise HubError("Console service is stopping")
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

    async def close_port(self, name: str, owner: str, *, expected_epoch: str | None = None) -> dict:
        async with self.lifecycle:
            port = self.get(name)
            if expected_epoch is not None and port.epoch != expected_epoch:
                raise HubError("Serial port was reopened; it was not closed")
            if port.clients or port.active_executions:
                raise HubError("Port has terminal clients or active execute calls")
            if port.writing or not port.writes.empty():
                raise HubError("Port has pending serial writes")
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
