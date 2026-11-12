import asyncio
import base64

import pytest

from serial_hub_mcp.core import HubError, PortConfig, port_name


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
    assert a == b
    assert a["text"] == "boot\r\n"
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


async def test_complete_transactions_are_fifo(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    loop = asyncio.get_running_loop()

    def respond(data):
        loop.call_later(0.08, device.input.put, data + b"\r\nroot# ")

    device.responder = respond
    results = await asyncio.gather(*[
        port.execute(str(i), f"cmd{i}", "root# ", timeout=2) for i in range(4)
    ])
    assert device.writes == [f"cmd{i}\r".encode() for i in range(4)]
    assert all(r["outcome"] == "matched" for r in results)
    assert all(f"cmd{i}" in r["text"] for i, r in enumerate(results))
    assert not port.uncertain and port.owner is None


async def test_timeout_requires_explicit_recovery_and_no_ctrl_c(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    result = await port.execute("a", "long_command", "root# ", timeout=0.1)
    assert result["outcome"] == "timeout" and result["needs_confirmation"]
    assert devices["COM3"].writes == [b"long_command\r"]
    with pytest.raises(HubError, match="unknown"):
        await port.acquire("b")
    await port.acknowledge("b")
    await port.acquire("b")
    assert port.owner == "b"


async def test_expired_manual_lease_does_not_mark_terminal_idle(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    await port.acquire("a")
    await port.send("a", b"interactive\r")
    port.deadline = 0
    with pytest.raises(HubError, match="unknown"):
        await port.acquire("b")
    assert devices["COM3"].writes == [b"interactive\r"]


async def test_human_takeover_interrupts_without_releasing_human(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    operation = asyncio.create_task(port.execute("ai", "slow", "root# ", timeout=5))
    await asyncio.sleep(0.05)
    await port.acquire("human", force=True)
    result = await asyncio.wait_for(operation, 1)
    assert result["outcome"] == "interrupted"
    assert port.owner == "human" and port.uncertain
    await port.send("human", b"\x03")
    assert devices["COM3"].writes == [b"slow\r", b"\x03"]


async def test_cancelled_execute_releases_but_preserves_unknown_state(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    operation = asyncio.create_task(port.execute("ai", "slow", "root# ", timeout=5))
    await asyncio.sleep(0.05)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert port.owner is None and port.uncertain


async def test_different_ports_can_execute_concurrently(rig):
    hub, devices = rig
    for name in ("COM3", "COM8"):
        await hub.open(name, PortConfig())
        device = devices[name]
        device.responder = lambda data, d=device: d.input.put(b"root# ")
    results = await asyncio.gather(*[
        hub.get(name).execute("a", "pwd", "root# ") for name in ("COM3", "COM8")
    ])
    assert all(r["outcome"] == "matched" for r in results)


async def test_disconnect_is_not_idle_and_does_not_retry(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    devices["COM3"].input.put(OSError("unplugged"))
    for _ in range(100):
        if not port.connected:
            break
        await asyncio.sleep(0.01)
    assert not port.connected and port.uncertain
    assert port.error == "unplugged"
    with pytest.raises(HubError, match="disconnected"):
        await port.acquire("a")
    assert devices["COM3"].writes == []


async def test_close_refuses_viewer_and_other_owner(rig):
    hub, _ = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    port.clients.add("viewer")
    with pytest.raises(HubError):
        await hub.close_port("COM3", "a")
    port.clients.clear()
    await port.acquire("b")
    with pytest.raises(HubError):
        await hub.close_port("COM3", "a")


@pytest.mark.parametrize("value", ["COM0", "COM65536", "../COM3", "loop://", "COM3;whoami"])
def test_reject_invalid_port_names(value):
    with pytest.raises(HubError):
        port_name(value)


async def test_no_writes_without_control(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    with pytest.raises(HubError, match="lease"):
        await hub.get("COM3").send("a", b"x")
    assert not devices["COM3"].writes


async def test_cancelled_queued_caller_does_not_block_next(rig):
    hub, _ = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    await port.acquire("a")
    waiting = asyncio.create_task(port.acquire("b", wait_seconds=2))
    await asyncio.sleep(0.03)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not port.queue
    await port.release("a")
    await port.acquire("c")
    assert port.owner == "c"


async def test_execute_under_existing_lease_keeps_reservation(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    device.responder = lambda data: device.input.put(b"root# ")
    await port.acquire("a")
    result = await port.execute("a", "pwd", "root# ")
    assert result["outcome"] == "matched" and port.owner == "a"
    with pytest.raises(HubError):
        await port.acquire("b")


async def test_confirmation_cannot_clear_another_controllers_state(rig):
    hub, _ = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    await port.acquire("a")
    await port.send("a", b"x")
    with pytest.raises(HubError):
        await port.acknowledge("b")
    assert port.uncertain


async def test_partial_write_preserves_uncertainty(rig):
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port, device = hub.get("COM3"), devices["COM3"]
    device.write = lambda data: len(data) - 1
    with pytest.raises(HubError, match="Partial"):
        await port.execute("a", "pwd", "root# ")
    assert port.uncertain and port.owner is None


async def test_journal_contains_exact_bytes_not_only_decoded_text(rig):
    import json
    from pathlib import Path
    hub, devices = rig
    await hub.open("COM3", PortConfig())
    port = hub.get("COM3")
    raw = b"\xff\x00\r\n"
    await receive(port, devices["COM3"], raw)
    await port.acquire("a")
    await port.send("a", raw)
    events = [json.loads(line) for line in Path(port.journal.path).read_text().splitlines()]
    assert [base64.b64decode(x["base64"]) for x in events if x["kind"] in ("rx", "tx")] == [raw, raw]
