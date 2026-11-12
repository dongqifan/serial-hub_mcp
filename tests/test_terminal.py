import asyncio

import telnetlib3
import pytest

from serial_hub_mcp.core import PortConfig
from serial_hub_mcp.terminal import Terminals


async def until(reader, needle, timeout=4):
    text = ""
    async with asyncio.timeout(timeout):
        while needle not in text:
            char = await reader.read(1)
            if not char:
                raise EOFError(text)
            text += char
    return text


async def connect(port):
    reader, writer = await telnetlib3.open_connection("127.0.0.1", port, connect_maxwait=0.1)
    banner = await until(reader, "no takeover keys.\r\n")
    assert "Access token" not in banner
    return reader, writer


async def test_all_telnet_clients_write_immediately_during_mcp_execute(rig):
    hub, devices = rig
    hub.terminals = Terminals(base_port=33000)
    state = await hub.open("COM3", PortConfig())
    endpoint = int(state["telnet"].rsplit(":", 1)[1])
    reader, writer = await connect(endpoint)
    other_reader, other_writer = await connect(endpoint)
    device, port = devices["COM3"], hub.get("COM3")
    operation = asyncio.create_task(port.execute("ai", "slow", "root# ", timeout=2))
    try:
        await asyncio.sleep(0.05)
        writer.write("human-a\r\n")
        other_writer.write("human-b\r\x00")
        await writer.drain()
        await other_writer.drain()
        await asyncio.sleep(0.1)
        combined = b"".join(device.writes)
        assert b"slow\r" in combined and b"human-a\r" in combined and b"human-b\r" in combined
        assert b"\n" not in combined and b"\x00" not in combined
        assert not operation.done()
        device.input.put(b"all clients see this\r\nroot# ")
        await until(reader, "all clients see this\r\n")
        await until(other_reader, "all clients see this\r\n")
        result = await operation
        assert result["outcome"] == "matched" and result["additional_writes_observed"] >= 2
        # Former management sequence is now ordinary serial input.
        writer.write("\x1da\x03")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert b"".join(device.writes).endswith(b"\x1da\x03")
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        writer.close()
        other_writer.close()


@pytest.mark.parametrize("first", ["\n", "\x00"])
async def test_telnet_first_input_is_not_consumed_as_login_or_cr_padding(rig, first):
    hub, devices = rig
    hub.terminals = Terminals(base_port=34000)
    state = await hub.open("COM3", PortConfig())
    endpoint = int(state["telnet"].rsplit(":", 1)[1])
    reader, writer = await telnetlib3.open_connection("127.0.0.1", endpoint, connect_maxwait=0.1)
    try:
        # Send before even reading the banner: no gateway login handshake.
        writer.write(first + "direct\r")
        await writer.drain()
        await until(reader, "no takeover keys.\r\n")
        for _ in range(100):
            if b"".join(devices["COM3"].writes).endswith(b"direct\r"):
                break
            await asyncio.sleep(0.01)
        assert b"".join(devices["COM3"].writes) == (first + "direct\r").encode()
        # CR padding can arrive in the next network chunk.
        writer.write("\nnext\r")
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write("\x00last\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert b"".join(devices["COM3"].writes) == (first + "direct\rnext\rlast\r").encode()
    finally:
        writer.close()
