import httpx

from serial_hub_mcp.daemon import create_app
from serial_hub_mcp.mcp_server import create_mcp


async def test_api_auth_origin_and_hardware_access(rig):
    hub, devices = rig
    token = "test-secret" * 4
    app = create_app(hub, token)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8766") as client:
        body = {"owner": "test", "operation": "open_port", "arguments": {"port": "COM3"}}
        assert (await client.post("/rpc", json=body)).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        denied = await client.post("/rpc", json=body, headers={**headers, "Origin": "https://evil.example"})
        assert denied.status_code == 403 and not devices
        bad_host = await client.post("/rpc", json=body, headers={**headers, "Host": "evil.example"})
        assert bad_host.status_code == 403
        response = await client.post("/rpc", json=body, headers=headers)
        assert response.status_code == 200 and "COM3" in devices
        assert devices["COM3"].writes == []
        body = {"owner": "a", "operation": "send", "arguments": {"port": "COM3", "text": "x"}}
        assert (await client.post("/rpc", json=body, headers=headers)).status_code == 409


async def test_mcp_tools_have_valid_schemas():
    mcp = create_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"list_ports", "open_port", "execute", "send", "read_output", "acquire_control"} <= names
    for tool in tools:
        assert "ctx" not in tool.inputSchema.get("properties", {})
