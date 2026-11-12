"""Real stdio MCP clients -> loopback HTTP -> fake serial (no hardware)."""

import asyncio
from contextlib import AsyncExitStack
import json
import socket
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn

from serial_hub_mcp.daemon import create_app
from serial_hub_mcp.settings import load_token


def result_data(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)


async def test_two_real_mcp_clients_share_serial_and_session_identity(rig, monkeypatch, tmp_path):
    hub, devices = rig
    monkeypatch.setenv("SERIAL_HUB_HOME", str(tmp_path / "state"))
    token = load_token(create=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    monkeypatch.setenv("SERIAL_HUB_API_PORT", str(port))
    server = uvicorn.Server(uvicorn.Config(create_app(hub, token), log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        async with AsyncExitStack() as stack:
            sessions = []
            for _ in range(2):
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=sys.executable, args=["-m", "serial_hub_mcp.mcp_server"],
                    env={"SERIAL_HUB_HOME": str(tmp_path / "state"), "SERIAL_HUB_API_PORT": str(port)},
                )))
                session = await stack.enter_async_context(ClientSession(*streams))
                await session.initialize()
                sessions.append(session)
            a, b = sessions
            meta_a = {"ai.opencode/sessionID": "session-a"}
            meta_b = {"ai.opencode/sessionID": "session-b"}
            await a.call_tool("open_port", {"port": "COM3"}, meta=meta_a)
            await b.call_tool("open_port", {"port": "COM3"}, meta=meta_b)
            result = result_data(await a.call_tool("acquire_control", {"port": "COM3"}, meta=meta_a))
            assert result["owner"] == "mcp:session-a"
            denied = await b.call_tool("send", {"port": "COM3", "text": "forbidden"}, meta=meta_b)
            assert denied.isError
            await a.call_tool("send", {"port": "COM3", "text": "pwd\r"}, meta=meta_a)
            devices["COM3"].input.put(b"/\r\nroot# ")
            await asyncio.sleep(0.05)
            ra = result_data(await a.call_tool("read_output", {"port": "COM3"}, meta=meta_a))
            rb = result_data(await b.call_tool("read_output", {"port": "COM3"}, meta=meta_b))
            assert ra["text"] == rb["text"] == "/\r\nroot# "
            assert devices["COM3"].writes == [b"pwd\r"]
            await a.call_tool("release_control", {"port": "COM3", "confirmed_idle": True}, meta=meta_a)
            result = result_data(await b.call_tool("acquire_control", {"port": "COM3"}, meta=meta_b))
            assert result["owner"] == "mcp:session-b"
        # Ending all MCP processes does not stop the shared serial connection.
        assert hub.get("COM3").connected and not devices["COM3"].closed
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()
