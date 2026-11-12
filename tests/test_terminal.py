import asyncio

import pytest
import telnetlib3

from serial_hub_mcp.core import HubError, PortConfig
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


async def connect(port, token):
    reader, writer = await telnetlib3.open_connection("127.0.0.1", port, connect_maxwait=0.1)
    await until(reader, "Access token: ")
    writer.write(token + "\r")
    await writer.drain()
    await until(reader, "discarded.\r\n")
    return reader, writer


async def test_telnet_watch_control_takeover_and_two_viewers(rig):
    hub, devices = rig
    token = "test-secret" * 4
    hub.terminals = Terminals(token, base_port=33000)
    state = await hub.open("COM3", PortConfig())
    endpoint = int(state["telnet"].rsplit(":", 1)[1])
    reader, writer = await connect(endpoint, token)
    other_reader, other_writer = await connect(endpoint, token)
    device, port = devices["COM3"], hub.get("COM3")
    try:
        writer.write("discarded\r")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert device.writes == []
        device.input.put(b"boot banner\r\n")
        await until(reader, "boot banner\r\n")
        await until(other_reader, "boot banner\r\n")
        writer.write("\x1da")
        await writer.drain()
        await until(reader, "CONTROL acquired")
        writer.write("pwd\r")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert b"".join(device.writes) == b"pwd\r"
        with pytest.raises(HubError):
            await port.acquire("ai")
        other_writer.write("\x1df")
        await other_writer.drain()
        await until(other_reader, "CONTROL acquired")
        assert port.owner.startswith("human:")
        writer.write("ignored")
        other_writer.write("x")
        await writer.drain()
        await other_writer.drain()
        await asyncio.sleep(0.1)
        assert b"".join(device.writes) == b"pwd\rx"
    finally:
        writer.close()
        other_writer.close()


async def test_telnet_bad_token_cannot_access_hardware(rig):
    hub, devices = rig
    hub.terminals = Terminals("test-secret" * 4, base_port=34000)
    state = await hub.open("COM3", PortConfig())
    endpoint = int(state["telnet"].rsplit(":", 1)[1])
    reader, writer = await telnetlib3.open_connection("127.0.0.1", endpoint, connect_maxwait=0.1)
    try:
        await until(reader, "Access token: ")
        writer.write("incorrect\r")
        await writer.drain()
        await until(reader, "Authentication failed")
        assert devices["COM3"].writes == []
        assert not hub.get("COM3").clients
    finally:
        writer.close()
