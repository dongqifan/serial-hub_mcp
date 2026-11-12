from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys

import httpx
import uvicorn

from .core import Hub, HubError, PortConfig, port_name
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


def rpc(operation: str, arguments: dict) -> dict:
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.post(api_url() + "/rpc",
                               json={"owner": "cli", "operation": operation, "arguments": arguments})
        if response.is_error:
            raise ValueError(response.json().get("error", f"Hub HTTP {response.status_code}"))
        return response.json()


def com_argument(value: str) -> str:
    try:
        return port_name(value)
    except HubError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def show_ports(inventory: dict) -> None:
    opened = {item["port"]: item for item in inventory["open"]}
    available = {item["port"]: item for item in inventory["available"]}
    print("PORT     HUB STATE      BAUD     TELNET                 CLIENTS EXECUTES")
    for name in sorted(available.keys() | opened.keys(), key=lambda name: (len(name), name)):
        item = opened.get(name)
        if item:
            state = "open" if item["connected"] else "disconnected"
            print(f"{name:<8} {state:<14} {item['config']['baudrate']:<8} "
                  f"{item['telnet'] or '-':<22} {item['terminal_clients']:<7} {item['active_executions']}")
        else:
            print(f"{name:<8} not-open       -        -                      -       -")
    print("not-open = not held by this hub; another program may still own it.")
    print("MCP readers are not persistent subscriptions and are not counted as clients.")


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
    status = commands.add_parser("status", help="Show daemon inventory or one shared COM's details (JSON)")
    status.add_argument("port", nargs="?", type=com_argument)
    ports = commands.add_parser("ports", help="List COM ports and this daemon's occupancy")
    ports.add_argument("--json", action="store_true", help="Output full inventory as JSON")
    opening = commands.add_parser("open", aliases=["occupy"], help="Open/hold COM in the shared daemon; sends no text")
    opening.add_argument("port", type=com_argument)
    opening.add_argument("--baudrate", type=int, default=115200)
    opening.add_argument("--bytesize", type=int, choices=[5, 6, 7, 8], default=8)
    opening.add_argument("--parity", choices=["N", "E", "O", "M", "S"], default="N")
    opening.add_argument("--stopbits", type=float, choices=[1, 1.5, 2], default=1)
    opening.add_argument("--flow-control", choices=["none", "rtscts", "xonxoff"], default="none")
    opening.add_argument("--dtr", action="store_true", help="Assert DTR (default low; hardware may reset)")
    opening.add_argument("--rts", action="store_true", help="Assert RTS (default low)")
    closing = commands.add_parser("close", aliases=["release"], help="Release COM only; refuses Telnet clients/active execute calls")
    closing.add_argument("port", type=com_argument)
    args = parser.parse_args()
    if args.command == "serve":
        try:
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            pass
    elif args.command == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    else:
        try:
            if args.command in ("open", "occupy"):
                config = PortConfig(**{name: getattr(args, name) for name in PortConfig.__dataclass_fields__})
                config.validate()
                result = rpc("open_port", {"port": args.port, "config": vars(config)})
            elif args.command in ("close", "release"):
                result = rpc("close_port", {"port": args.port})
            elif args.command == "status" and args.port:
                result = rpc("port_status", {"port": args.port})
            else:
                result = rpc("list_ports", {})
            if args.command == "ports" and not args.json:
                show_ports(result)
            else:
                print(json.dumps(result, ensure_ascii=True, indent=2))
        except httpx.HTTPError as exc:
            print(f"Serial Hub daemon unavailable at {api_url()}: {exc}. Start the service first.", file=sys.stderr)
            raise SystemExit(1) from exc
        except (ValueError, HubError) as exc:
            print(f"Serial Hub: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
