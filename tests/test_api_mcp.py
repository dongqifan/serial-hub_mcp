import asyncio
from types import SimpleNamespace

import httpx
import uvicorn

from serial_hub_mcp import cli

from serial_hub_mcp.daemon import create_app
from serial_hub_mcp.mcp_server import create_mcp


async def test_api_without_auth_preserves_origin_and_host_checks(rig):
    hub, devices = rig
    app = create_app(hub)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766") as client:
        body = {"owner": "test", "operation": "open_port", "arguments": {"port": "COM3"}}
        denied = await client.post("/rpc", json=body, headers={"Origin": "https://evil.example"})
        assert denied.status_code == 403 and not devices
        bad_host = await client.post("/rpc", json=body, headers={"Host": "evil.example"})
        assert bad_host.status_code == 403
        response = await client.post("/rpc", json=body)
        assert response.status_code == 200 and "COM3" in devices
        assert devices["COM3"].writes == []
        body = {"owner": "a", "operation": "send", "arguments": {"port": "COM3", "text": "x"}}
        assert (await client.post("/rpc", json=body)).status_code == 200
        assert devices["COM3"].writes == [b"x"]
        for op in ("acquire_control", "release_control", "acknowledge_state"):
            body = {"owner": "legacy", "operation": op, "arguments": {"port": "COM3"}}
            result = await client.post("/rpc", json=body)
            assert result.status_code == 200 and result.json()["input_mode"] == "shared"
        # Old adapters may still send a header; it is ignored, not validated.
        assert (await client.post("/rpc", json=body, headers={"Authorization": "Bearer unused"})).status_code == 200


async def test_idle_stop_reaches_only_the_selected_running_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    monkeypatch.setattr(cli, 'api_url', lambda: 'http://127.0.0.1:0')
    ready = asyncio.get_running_loop().create_future()
    startup = uvicorn.Server.startup

    async def started(server, sockets=None):
        await startup(server, sockets=sockets)
        ready.set_result((server, sockets[0].getsockname()[1]))

    monkeypatch.setattr(uvicorn.Server, 'startup', started)
    task = asyncio.create_task(cli.serve(SimpleNamespace(telnet_base=2300, encoding='utf-8', port=[])))
    try:
        server, port = await asyncio.wait_for(asyncio.shield(ready), 5)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
            async def request(op, args):
                return await client.post('/rpc', json={'owner': 'test', 'operation': op, 'arguments': args})
            status = (await request('console_status', {})).json()
            assert 'idle_stop' in status['capabilities'] and status['open_ports'] == []
            bad = await request('console_stop', {'instance_id': 'different-instance'})
            assert bad.status_code == 409 and not server.should_exit
            stopped = await request('console_stop', {'instance_id': status['instance_id']})
            assert stopped.status_code == 200 and stopped.json() == {'stopping': True}
        await asyncio.wait_for(asyncio.shield(task), 5)
    finally:
        if not task.done():
            if ready.done() and not ready.cancelled():
                ready.result()[0].should_exit = True
            else:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_mcp_tools_have_valid_schemas():
    mcp = create_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"list_ports", "open_port", "port_status", "execute", "send", "send_bytes", "read_output", "close_port"} <= names
    assert {"get_project", "open_board_serial", "read_board_output", "send_board_text", "send_board_bytes", "execute_board", "close_board_serial"} <= names
    assert not {"acquire_control", "release_control", "acknowledge_state"} & names
    assert not any(name.startswith('console_') for name in names)
    for tool in tools:
        fields = tool.inputSchema.get("properties", {})
        assert "ctx" not in fields
        if tool.name in {"read_board_output", "send_board_text", "send_board_bytes", "execute_board", "close_board_serial"}:
            assert {'project_id', 'board_id', 'connection_id'} <= set(tool.inputSchema['required'])
            assert not {'port', 'config'} & fields.keys()


async def test_mcp_guidance_warns_about_console_readiness():
    mcp = create_mcp()
    instructions = mcp._mcp_server.instructions
    assert "askfirst" in instructions
    assert "successful write does not prove" in instructions
    assert "Enter can execute pending input" in instructions
    assert "never automatically retry" in instructions
    tools = {tool.name: tool.description for tool in await mcp.list_tools()}
    for name in ("send", "execute"):
        assert "askfirst" in tools[name]
        assert "Enter can execute pending input" in tools[name]
    assert "shared" in tools["execute"].lower()
