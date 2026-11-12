from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys

import httpx
import uvicorn

from .core import Hub, PortConfig
from .daemon import create_app
from .settings import api_url, state_directory
from .terminal import Terminals


async def serve(args) -> None:
    # Reserve the API endpoint before opening hardware: a duplicate daemon must
    # not open new ports and only then discover another instance owns the API.
    sock = socket.socket()
    sock.bind(("127.0.0.1", int(api_url().rsplit(":", 1)[1])))
    sock.listen(128)
    hub = Hub(state_directory() / "logs")
    hub.terminals = Terminals(args.telnet_base, args.encoding)
    try:
        for name in args.port:
            result = await hub.open(name, PortConfig(baudrate=args.baudrate))
            print(f"{result['port']}: Telnet {result['telnet']}", flush=True)
        print(f"Serial Hub API: {api_url()} (loopback only)", flush=True)
        print("Authentication disabled; local clients connect directly.", flush=True)
        config = uvicorn.Config(create_app(hub), host="127.0.0.1",
                                port=int(api_url().rsplit(":", 1)[1]), access_log=False)
        await uvicorn.Server(config).serve(sockets=[sock])
    finally:
        await hub.shutdown()
        sock.close()


def main() -> None:
    # pythonw.exe (logon task) has no stdout/stderr. Uvicorn's formatter needs
    # real streams; keep diagnostics local instead of failing at startup.
    if sys.stdout is None or sys.stderr is None:
        directory = state_directory()
        directory.mkdir(parents=True, exist_ok=True)
        if sys.stdout is None:
            sys.stdout = open(directory / "daemon.stdout.log", "a", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = open(directory / "daemon.stderr.log", "a", encoding="utf-8", buffering=1)
    parser = argparse.ArgumentParser(description="Shared serial daemon and MCP adapter (Windows COM ports)")
    commands = parser.add_subparsers(dest="command", required=True)
    daemon = commands.add_parser("serve", help="Run the shared daemon independently of OpenCode")
    daemon.add_argument("--port", action="append", default=[], help="Optionally open COMx on startup; repeatable")
    daemon.add_argument("--baudrate", type=int, default=115200)
    daemon.add_argument("--telnet-base", type=int, default=2300)
    daemon.add_argument("--encoding", default="utf-8", help="Xshell text encoding (MCP can choose per call)")
    commands.add_parser("mcp", help="Run lightweight stdio MCP adapter; never opens hardware itself")
    commands.add_parser("status", help="Show daemon inventory")
    args = parser.parse_args()
    if args.command == "serve":
        try:
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            pass
    elif args.command == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    elif args.command == "status":
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(api_url() + "/rpc",
                                   json={"owner": "cli", "operation": "list_ports", "arguments": {}})
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
