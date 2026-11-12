from __future__ import annotations

import uuid

import httpx
from mcp.server.fastmcp import Context, FastMCP

from .settings import api_url, load_token


def create_mcp() -> FastMCP:
    fallback = "client:" + uuid.uuid4().hex
    mcp = FastMCP(
        "Serial Hub",
        instructions=(
            "Shared Windows COM ports. The daemon, not this MCP process, owns hardware. "
            "Use only the user's requested COM port and baud settings. No input is sent on open/read. "
            "Independent read cursors allow all sessions to observe the same RX data. "
            "Use execute with an observed literal terminal prompt as expect; matching text is NOT an exit code. "
            "Never assume a timeout means idle; inspect output and explicitly acknowledge_state before recovery. "
            "Human terminal takeover can interrupt commands. Do not retry interrupted writes automatically. "
            "Use acquire_control/send/release_control for interactive work. Do not close a port other sessions use. "
            "Raw text/base64 and local JSONL logs may contain credentials; do not publish them."
        ),
    )

    async def call(ctx: Context, operation: str, arguments: dict) -> dict:
        meta = ctx.request_context.meta
        values = meta.model_dump(by_alias=True) if meta else {}
        owner = values.get("ai.opencode/sessionID", fallback)
        if not isinstance(owner, str) or not owner:
            owner = fallback
        try:
            token = load_token()
            async with httpx.AsyncClient(timeout=httpx.Timeout(200, connect=3), trust_env=False) as client:
                response = await client.post(api_url() + "/rpc", headers={"Authorization": "Bearer " + token},
                                             json={"owner": owner, "operation": operation, "arguments": arguments})
                data = response.json()
                if response.is_error:
                    raise ValueError(data.get("error", f"Hub HTTP {response.status_code}"))
                return data
        except (OSError, httpx.HTTPError) as exc:
            raise ValueError("Serial Hub daemon is unavailable. Start 'serial-hub serve' first. " + str(exc)) from exc

    @mcp.tool()
    async def list_ports(ctx: Context) -> dict:
        """Enumerate Windows COM ports and shared open connections; does not open hardware."""
        return await call(ctx, "list_ports", {})

    @mcp.tool()
    async def open_port(port: str, ctx: Context, baudrate: int = 115200, bytesize: int = 8,
                        parity: str = "N", stopbits: float = 1, flow_control: str = "none",
                        dtr: bool = False, rts: bool = False) -> dict:
        """Open/reuse COMx, start RX capture, return Xshell Telnet endpoint. Never sends text.

        Confirm serial parameters with user; driver/hardware may glitch DTR/RTS on open.
        Existing different settings are rejected, not silently replaced.
        """
        return await call(ctx, "open_port", {"port": port, "config": {
            "baudrate": baudrate, "bytesize": bytesize, "parity": parity, "stopbits": stopbits,
            "flow_control": flow_control, "dtr": dtr, "rts": rts}})

    @mcp.tool()
    async def port_status(port: str, ctx: Context) -> dict:
        """Inspect controller, lease, terminal clients, RX cursors and disconnect state."""
        return await call(ctx, "port_status", {"port": port})

    @mcp.tool()
    async def read_output(port: str, ctx: Context, cursor: str | None = None,
                          max_bytes: int = 16384, wait_seconds: float = 0,
                          encoding: str = "utf-8") -> dict:
        """Read RX without consuming other sessions' data. Reuse returned cursor for increments.

        No cursor replays retained buffer. base64 is lossless; text can split UTF-8 chunks.
        Old cursors are rejected after close/reopen. bytes_lost reports ring overflow.
        """
        return await call(ctx, "read_output", {"port": port, "cursor": cursor, "max_bytes": max_bytes,
                                               "wait_seconds": wait_seconds, "encoding": encoding})

    @mcp.tool()
    async def acquire_control(port: str, ctx: Context, lease_seconds: float = 120,
                              wait_seconds: float = 0) -> dict:
        """Acquire/renew exclusive input lease. Other readers remain unaffected; no forced AI takeover."""
        return await call(ctx, "acquire_control", {"port": port, "lease_seconds": lease_seconds,
                                                   "wait_seconds": wait_seconds})

    @mcp.tool()
    async def release_control(port: str, ctx: Context, confirmed_idle: bool = False) -> dict:
        """Release your lease. Confirm idle only after observing the terminal; otherwise preserve uncertainty."""
        return await call(ctx, "release_control", {"port": port, "confirmed_idle": confirmed_idle})

    @mcp.tool()
    async def acknowledge_state(port: str, confirmed_idle: bool, ctx: Context) -> dict:
        """Explicitly confirm observed terminal is idle after timeout/manual input. Does not send anything.

        Allowed only when no other controller/operation is active. This is not a reset command.
        """
        return await call(ctx, "acknowledge_state", {"port": port, "confirmed_idle": confirmed_idle})

    @mcp.tool()
    async def send(port: str, text: str, ctx: Context, encoding: str = "utf-8") -> dict:
        """Send exact text/control characters under your lease (no implicit newline). Leaves state uncertain."""
        return await call(ctx, "send", {"port": port, "text": text, "encoding": encoding})

    @mcp.tool()
    async def send_bytes(port: str, base64_data: str, ctx: Context) -> dict:
        """Send exact base64-encoded bytes under your lease. Never resets, flushes or retries automatically."""
        return await call(ctx, "send", {"port": port, "base64": base64_data})

    @mcp.tool()
    async def execute(port: str, text: str, expect: str, ctx: Context, timeout: float = 10,
                      wait_seconds: float = 30, newline: str = "\r", encoding: str = "utf-8") -> dict:
        """Queue one command transaction and collect RX until literal expect, timeout or human takeover.

        Requires a known idle terminal; use a specific observed prompt (not command echo) for expect.
        Result is bounded to 64 KiB; full RX remains in local logs. No automatic Ctrl+C on timeout.
        Automatically releases only a lease acquired by this call, not a previously held lease.
        """
        return await call(ctx, "execute", {"port": port, "text": text, "expect": expect,
                                           "timeout": timeout, "wait_seconds": wait_seconds,
                                           "newline": newline, "encoding": encoding})

    @mcp.tool()
    async def close_port(port: str, ctx: Context) -> dict:
        """Explicitly close idle unused port. Refuses other controllers, Telnet viewers or queued operations.

        Passive MCP readers are not tracked as subscriptions: confirm nobody still needs capture first.
        """
        return await call(ctx, "close_port", {"port": port})

    return mcp


def main() -> None:
    create_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
