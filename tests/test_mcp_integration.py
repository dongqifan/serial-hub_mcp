"""Real stdio MCP clients -> loopback HTTP -> fake serial (no hardware)."""

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import socket
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn
import pytest

from serial_hub_mcp.daemon import create_app


def result_data(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)


@pytest.mark.parametrize("legacy_token", [False, True])
async def test_two_real_mcp_clients_share_serial_and_session_identity(rig, monkeypatch, tmp_path, legacy_token):
    hub, devices = rig
    monkeypatch.setenv("SERIAL_HUB_HOME", str(tmp_path / "state"))
    old_file = tmp_path / "state" / "access.token"
    if legacy_token:
        old_file.parent.mkdir()
        old_file.write_bytes(b"\xffinvalid legacy token")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    monkeypatch.setenv("SERIAL_HUB_API_PORT", str(port))
    server = uvicorn.Server(uvicorn.Config(create_app(hub), log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "serial_hub_mcp.cli", "status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0, stderr
        assert isinstance(json.loads(stdout), dict)
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
            results = await asyncio.gather(
                a.call_tool("send", {"port": "COM3", "text": "from-a\r"}, meta=meta_a),
                b.call_tool("send", {"port": "COM3", "text": "from-b\r"}, meta=meta_b),
            )
            assert all(not r.isError for r in results)
            devices["COM3"].input.put(b"/\r\nroot# ")
            await asyncio.sleep(0.05)
            ra = result_data(await a.call_tool("read_output", {"port": "COM3"}, meta=meta_a))
            rb = result_data(await b.call_tool("read_output", {"port": "COM3"}, meta=meta_b))
            assert ra["text"] == rb["text"] == "/\r\nroot# "
            assert set(devices["COM3"].writes) == {b"from-a\r", b"from-b\r"}
            events = [json.loads(line) for line in Path(hub.get("COM3").journal.path).read_text().splitlines()]
            assert {x["source"] for x in events if x["kind"] == "tx"} == {"mcp:session-a", "mcp:session-b"}
        # Ending all MCP processes does not stop the shared serial connection.
        assert hub.get("COM3").connected and not devices["COM3"].closed
        if legacy_token:
            assert old_file.read_bytes() == b"\xffinvalid legacy token"
        else:
            assert not old_file.parent.exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()
