from __future__ import annotations

import base64
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .core import Hub, HubError, PortConfig
from .project_service import BOARD_OPS, GUI_OPS, ProjectService


async def dispatch(console: ProjectService, operation: str, args: dict, owner: str) -> dict:
    hub = console.hub
    if operation in BOARD_OPS:
        return await console.board_rpc(operation, args, owner)
    if operation in GUI_OPS:
        return await console.management(operation, args)
    if operation == "list_ports":
        return await hub.inventory()
    if operation == "open_port":
        config = PortConfig(**args.get("config", {}))
        return await hub.open(args["port"], config)
    port = hub.get(args["port"])
    if operation == "port_status":
        return port.snapshot()
    if operation == "read_output":
        return await port.read(args.get("cursor"), args.get("max_bytes", 16384),
                               args.get("wait_seconds", 0), args.get("encoding", "utf-8"))
    if operation in ("acquire_control", "release_control", "acknowledge_state"):
        # Compatibility for v0.1 adapters already running in other sessions.
        # No locks, gating or confirmation exists in shared mode.
        return {**port.snapshot(), "deprecated": "Control is no longer required; all clients can write immediately."}
    if operation == "send":
        if "base64" in args:
            data = base64.b64decode(args["base64"], validate=True)
        else:
            data = args["text"].encode(args.get("encoding", "utf-8"))
        return await port.send(owner, data)
    if operation == "execute":
        return await port.execute(owner, args["text"], args["expect"], args.get("timeout", 10),
                                  args.get("wait_seconds", 30), args.get("newline", "\r"),
                                  args.get("encoding", "utf-8"))
    if operation == "close_port":
        return await hub.close_port(port.name, owner)
    raise HubError("Unknown operation")


def create_app(hub: Hub, *, request_stop=None) -> Starlette:
    console = ProjectService(hub, request_stop)

    async def rpc(request: Request):
        # No browser-origin requests, even if a malicious page knows the URL.
        if request.headers.get("origin"):
            return JSONResponse({"error": "Browser origins are not allowed"}, status_code=403)
        if request.headers.get("host") not in {"127.0.0.1", "localhost", f"127.0.0.1:{request.url.port}", f"localhost:{request.url.port}"}:
            return JSONResponse({"error": "Invalid Host"}, status_code=403)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 262144:
                return JSONResponse({"error": "Request too large"}, status_code=413)
        try:
            import json
            body = json.loads(raw)
            if not isinstance(body, dict) or not isinstance(body.get("arguments", {}), dict):
                raise HubError("Request and arguments must be objects")
            owner = body.get("owner", "")
            if not isinstance(owner, str) or not 1 <= len(owner) <= 200:
                raise HubError("Invalid controller identity")
            result = await dispatch(console, body["operation"], body.get("arguments", {}), "mcp:" + owner)
            return JSONResponse(result)
        except (HubError, ValueError, TypeError, KeyError, LookupError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await hub.shutdown()

    return Starlette(routes=[Route("/rpc", rpc, methods=["POST"])], lifespan=lifespan)
