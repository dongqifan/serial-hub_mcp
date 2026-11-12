import httpx

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


async def test_mcp_tools_have_valid_schemas():
    mcp = create_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"list_ports", "open_port", "execute", "send", "read_output"} <= names
    assert not {"acquire_control", "release_control", "acknowledge_state"} & names
    for tool in tools:
        assert "ctx" not in tool.inputSchema.get("properties", {})
