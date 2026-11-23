import asyncio
import base64
import json
from functools import partial
from pathlib import Path
import threading

import pytest

from serial_hub_mcp.core import HubError, Journal, PortConfig, port_name


async def receive(port, device, data):
    before = port.end
    device.input.put(data)
    for _ in range(100):
        if port.end >= before + len(data):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Reader did not receive data")


async def test_open_reuses_without_transmit_and_rejects_config_change(rig):
    hub, devices = rig
    first = await hub.open("com3", PortConfig())
    second = await hub.open("COM3", PortConfig())
    assert first["cursor_end"] == second["cursor_end"]
    assert first["input_mode"] == "shared"
    assert devices["COM3"].writes == []
    with pytest.raises(HubError, match="different parameters"):
        await hub.open("COM3", PortConfig(baudrate=9600))


async def test_independent_readers_and_overflow(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    start = port.cursor(0)
    await receive(port, devices["COM3"], b"boot\r\n")
    a, b = await asyncio.gather(port.read(start), port.read(start))
    assert a == b and a["text"] == "boot\r\n"
    await receive(port, devices["COM3"], b"x" * 200)
    overflow = await port.read(start)
    assert overflow["bytes_lost"] == 78
    assert len(base64.b64decode(overflow["base64"])) == 128


async def test_cursor_rejects_previous_opening(rig):
    hub, _ = rig
    state = await hub.open("COM3", PortConfig())
    await hub.close_port("COM3", "a")
    await hub.open("COM3", PortConfig())
    with pytest.raises(HubError, match="earlier"):
        await hub.get("COM3").read(state["cursor_end"])


async def test_all_execute_calls_write_before_any_response(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    operations = [asyncio.create_task(port.execute(str(i), f"cmd{i}", "root# ", timeout=2)) for i in range(4)]
    try:
        await asyncio.sleep(0.05)
        # No queue for command completion: all four writes precede the first RX.
        assert device.writes == [f"cmd{i}\r".encode() for i in range(4)]
        assert port.active_executions == 4
        assert all(not op.done() for op in operations)
        device.input.put(b"shared response\r\nroot# ")
        results = await asyncio.gather(*operations)
        assert all(r["outcome"] == "matched" and r["shared_output"] for r in results)
        assert [r["additional_writes_observed"] for r in results] == [3, 2, 1, 0]
        assert len({r["text"] for r in results}) == 1
        assert port.active_executions == 0
    finally:
        for op in operations:
            op.cancel()
        await asyncio.gather(*operations, return_exceptions=True)


async def test_send_during_execute_is_immediate_and_does_not_cancel_it(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    operation = asyncio.create_task(port.execute("ai", "slow", "root# ", timeout=2))
    await asyncio.sleep(0.05)
    await port.send("human", b"human\r")
    await port.send("other-ai", b"other\r")
    assert device.writes == [b"slow\r", b"human\r", b"other\r"]
    assert not operation.done()
    device.input.put(b"root# ")
    result = await operation
    assert result["outcome"] == "matched" and result["additional_writes_observed"] == 2


async def test_timeout_does_not_gate_next_writer_or_send_ctrl_c(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    result = await port.execute("a", "long_command", "root# ", timeout=0.1)
    assert result["outcome"] == "timeout"
    await port.send("b", b"new input\r")
    device.responder = lambda data: device.input.put(b"root# ")
    assert (await port.execute("c", "pwd", "root# "))["outcome"] == "matched"
    assert device.writes == [b"long_command\r", b"new input\r", b"pwd\r"]


async def test_cancelled_execute_does_not_stop_other_clients(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    operation = asyncio.create_task(port.execute("ai", "slow", "root# ", timeout=5))
    await asyncio.sleep(0.05)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert port.active_executions == 0
    await port.send("b", b"x")
    assert devices["COM3"].writes == [b"slow\r", b"x"]


async def test_different_ports_execute_concurrently(rig):
    hub, devices = rig
    for name in ("COM3", "COM8"):
        await hub.open(name, PortConfig())
        devices[name].responder = lambda data, d=devices[name]: d.input.put(b"root# ")
    results = await asyncio.gather(*[hub.get(name).execute("a", "pwd", "root# ") for name in ("COM3", "COM8")])
    assert all(r["outcome"] == "matched" for r in results)


async def test_disconnect_does_not_retry(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    devices["COM3"].input.put(OSError("unplugged"))
    for _ in range(100):
        if not port.connected:
            break
        await asyncio.sleep(0.01)
    assert not port.connected and port.error == "unplugged"
    with pytest.raises(HubError, match="disconnected"):
        await port.send("a", b"x")
    assert devices["COM3"].writes == []


async def test_close_refuses_viewer_or_active_execution(rig):
    hub, _ = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    port.clients.add("viewer")
    with pytest.raises(HubError):
        await hub.close_port("COM3", "a")
    port.clients.clear()
    port.active_executions = 1
    with pytest.raises(HubError):
        await hub.close_port("COM3", "a")
    port.active_executions = 0
    await hub.close_port("COM3", "anyone")


@pytest.mark.parametrize("value", ["COM0", "COM65536", "../COM3", "loop://", "COM3;whoami"])
def test_reject_invalid_port_names(value):
    with pytest.raises(HubError):
        port_name(value)


async def test_every_client_can_send_without_acquisition(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    await asyncio.gather(*[port.send(str(i), str(i).encode()) for i in range(5)])
    assert devices["COM3"].writes == [str(i).encode() for i in range(5)]


async def test_partial_write_does_not_block_future_writes(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    original = device.write
    device.write = lambda data: len(data) - 1
    with pytest.raises(HubError, match="Partial"):
        await port.execute("a", "pwd", "root# ")
    assert port.active_executions == 0
    device.write = original
    await port.send("b", b"x")
    assert device.writes == [b"x"]


async def test_blocked_write_does_not_block_receive_or_another_port(rig):
    hub, devices = rig
    for name in ("COM3", "COM8"):
        await hub.open(name, PortConfig())
    port = hub.get("COM3")
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    write = devices["COM3"].write

    def block_write(data):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return write(data)

    devices["COM3"].write = block_write
    sending = asyncio.create_task(port.send("a", b"first"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(HubError, match="pending serial writes"):
            await hub.close_port("COM3", "a")
        cursor = port.cursor(port.end)
        devices["COM3"].input.put(b"receive while writing")
        assert (await port.read(cursor, wait_seconds=1))["text"] == "receive while writing"
        assert (await asyncio.wait_for(hub.get("COM8").send("b", b"other port"), 1))["written"] == 10
        assert not sending.done()
    finally:
        release.set()
        await sending


async def test_writer_queue_is_bounded_and_skips_cancelled_pending_input(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    write = devices["COM3"].write

    def block_first(data):
        if data == b"first":
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
        return write(data)

    devices["COM3"].write = block_first
    first = asyncio.create_task(port.send("a", b"first"))
    pending = []
    try:
        await asyncio.wait_for(started.wait(), 2)
        blocks = [str(i).encode() for i in range(port.writes.maxsize)]
        pending = [asyncio.create_task(port.send("b", block)) for block in blocks]
        await asyncio.sleep(0)
        with pytest.raises(HubError, match="queue is full"):
            await port.send("c", b"rejected")
        first.cancel()
        pending[0].cancel()
        cancelled = await asyncio.gather(first, pending[0], return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in cancelled)
        assert port.writing and not devices["COM3"].writes
    finally:
        release.set()
        await asyncio.gather(first, *pending, return_exceptions=True)
    assert devices["COM3"].writes == [b"first", *blocks[1:]]
    assert port.write_sequence == len(blocks)


async def test_busy_journal_keeps_rx_available_and_marks_gaps(rig, monkeypatch):
    hub, devices = rig
    monkeypatch.setattr("serial_hub_mcp.core.Journal", partial(Journal, queue_size=1))
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    journal = port.journal
    await asyncio.wait_for(journal.pending.join(), 2)
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    emit = journal._emit

    def block_emit(*args, **fields):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        emit(*args, **fields)

    journal._emit = block_emit
    try:
        await receive(port, devices["COM3"], b"a")
        await asyncio.wait_for(started.wait(), 2)
        await receive(port, devices["COM3"], b"b")
        await receive(port, devices["COM3"], b"c")
        assert (await port.read())["text"] == "abc"
        assert port.snapshot()["journal_dropped_events"] == 1
    finally:
        release.set()
    await asyncio.wait_for(journal.pending.join(), 2)
    await receive(port, devices["COM3"], b"d")
    await asyncio.wait_for(journal.pending.join(), 2)
    events = [json.loads(line) for line in Path(journal.path).read_text().splitlines()]
    gap, last = events[-2:]
    assert gap["kind"] == "journal_gap" and gap["events_lost"] == 1
    assert last["epoch"] == gap["epoch"] == port.epoch and last["end"] == 4
    assert base64.b64decode(last["base64"]) == b"d"


async def test_journal_contains_exact_bytes_and_source(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    raw = b"\xff\x00\r\n"
    await receive(port, devices["COM3"], raw)
    await port.send("source-a", raw)
    await asyncio.wait_for(port.journal.pending.join(), 2)
    events = [json.loads(line) for line in Path(port.journal.path).read_text().splitlines()]
    assert [base64.b64decode(x["base64"]) for x in events if x["kind"] in ("rx", "tx")] == [raw, raw]
    assert next(x for x in events if x["kind"] == "tx")["source"] == "source-a"
