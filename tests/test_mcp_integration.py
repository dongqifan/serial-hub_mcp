"""Real stdio MCP clients -> loopback HTTP -> fake serial (no hardware)."""

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn
import pytest

from serial_hub_mcp import boards
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
    app = create_app(hub)
    sources = []

    async def trace(scope, receive, send):
        if scope["type"] == "http":
            sources.append(scope["client"])
        await app(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(trace, log_level="error", access_log=False))
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
        async def run_cli(*arguments, success=True):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "serial_hub_mcp.cli", *arguments,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == (0 if success else 1), stderr
            return json.loads(stdout) if success else stderr.decode()

        opened = await run_cli("occupy", "COM3", "--baudrate", "9600")
        assert opened["config"]["baudrate"] == 9600
        assert devices["COM3"].writes == []
        assert (await run_cli("ports", "--json"))["open"][0]["port"] == "COM3"
        assert (await run_cli("status", "COM3"))["input_mode"] == "shared"
        hub.get("COM3").clients.add("test-viewer")
        assert "terminal clients" in await run_cli("release", "COM3", success=False)
        assert not devices["COM3"].closed
        hub.get("COM3").clients.clear()
        assert (await run_cli("release", "COM3")) == {"closed": "COM3"}
        assert devices["COM3"].closed
        assert (await run_cli("status"))["open"] == []
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
            before = len(sources)
            for _ in range(3):
                result_data(await a.call_tool("list_ports", {}))
            assert len(sources[before:]) == 3
            assert len(set(sources[before:])) == 1
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
            journal = hub.get("COM3").journal
            await asyncio.wait_for(journal.pending.join(), 2)
            events = [json.loads(line) for line in Path(journal.path).read_text().splitlines()]
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


async def test_agent_uses_only_a_project_id_to_discover_and_talk_to_its_board(rig, monkeypatch, tmp_path):
    hub, devices = rig
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path / 'profiles'))
    monkeypatch.setattr('serial_hub_mcp.core.list_ports.comports', lambda: [
        SimpleNamespace(device='COM7', description='test fixture', hwid='fake')])
    project = boards.new_project('考场 A')
    project.boards.append(dict(boards.new_board(), name='DUT', port='COM7',
                               web_url='http://board.test', serial_user='account-not-for-agents'))
    boards.save_catalog([project], expected_revision=boards.load_catalog().revision)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(create_app(hub), log_level='error', access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(.01)
        spec = StdioServerParameters(command=sys.executable, args=['-I', '-m', 'serial_hub_mcp.mcp_server'],
                                     env={'SERIAL_HUB_API_PORT': str(sock.getsockname()[1])})
        async with stdio_client(spec) as streams, ClientSession(*streams) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            assert {'get_project', 'open_board_serial', 'read_board_output', 'send_board_text', 'execute_board'} <= names
            assert not any(t.startswith('console_') for t in names)
            template = 'board-console://projects/{project_id}'
            assert template in {r.uriTemplate for r in (await client.list_resource_templates()).resourceTemplates}
            ids = {'project_id': project.id}
            info = result_data(await client.call_tool('get_project', ids))
            resource = await client.read_resource(template.format(**ids))
            assert json.loads(resource.contents[0].text) == info
            assert 'account-not-for-agents' not in resource.contents[0].text
            assert not devices
            item, = info['boards']
            ids['board_id'] = item['board_id']
            opened = result_data(await client.call_tool('open_board_serial',
                {**ids, 'binding_id': item['serial']['binding_id']}))
            assert devices['COM7'].writes == []
            handle = {**ids, 'connection_id': opened['connection_id']}
            devices['COM7'].responder = lambda data: devices['COM7'].input.put(b'ready\r\n# ')
            match = result_data(await client.call_tool('execute_board',
                {**handle, 'text': 'test', 'expect': '\r\n# '}))
            assert match['outcome'] == 'matched' and devices['COM7'].writes == [b'test\r']
            output = result_data(await client.call_tool('read_board_output',
                {**handle, 'cursor': opened['cursor_end']}))
            assert output['text'] == 'ready\r\n# '
            catalog = boards.load_catalog()
            catalog.by_id(project.id).name = '考场 B'
            boards.save_catalog(catalog.projects, expected_revision=catalog.revision)
            updated = json.loads((await client.read_resource(template.format(**ids))).contents[0].text)
            assert updated['name'] == '考场 B' and updated['boards'][0]['board_id'] == ids['board_id']
            assert (await client.call_tool('get_project', {'project_id': 'invalid'})).isError
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()
