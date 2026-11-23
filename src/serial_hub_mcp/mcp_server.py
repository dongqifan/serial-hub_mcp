from __future__ import annotations

from contextlib import asynccontextmanager
import uuid

import httpx
from mcp.server.fastmcp import Context, FastMCP

from .settings import api_url


@asynccontextmanager
async def http_lifespan(_server):
    async with httpx.AsyncClient(timeout=httpx.Timeout(200, connect=3), trust_env=False) as client:
        yield client


def create_mcp() -> FastMCP:
    fallback = "client:" + uuid.uuid4().hex
    mcp = FastMCP(
        "Serial Hub",
        lifespan=http_lifespan,
        instructions=(
            "Shared Windows COM ports. The daemon, not this MCP process, owns hardware. "
            "Use only the user's requested COM port and baud settings. No input is sent on open/read. "
            "A successful write does not prove a shell received or ran a command: an askfirst console may consume "
            "it while waiting for Enter. A stale prompt or echoed input is not proof of readiness. "
            "Enter can execute pending input; inspect the current context and coordinate with other users first. "
            "Before consequential commands, verify the target and current shell using an appropriate harmless "
            "marked response when safe; shared output cannot prove attribution. "
            "Afterwards verify the requested outcome independently; never automatically retry uncertain writes. "
            "Independent read cursors allow all sessions to observe the same RX data. "
            "Use execute with an observed literal terminal prompt as expect; matching text is NOT an exit code. "
            "All MCP sessions and Telnet clients can write without leases or waiting for a command response. "
            "Each port queues complete write blocks in arrival order. "
            "Input can mix in the single terminal. execute observes shared RX, NOT a private command response; "
            "another client's output can satisfy expect. A timeout never proves idle; do not retry writes automatically. "
            "Use send directly for interactive input. Do not close a port other sessions use. "
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
            client = ctx.request_context.lifespan_context
            response = await client.post(api_url() + "/rpc",
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
        """Inspect shared input mode, active executions, terminal clients, RX cursors and disconnect state."""
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
    async def send(port: str, text: str, ctx: Context, encoding: str = "utf-8") -> dict:
        """Queue exact text/control characters (no implicit newline) and await the driver write.

        A successful write does not prove a shell received or executed it; askfirst may consume input.
        Enter can execute pending input. Inspect context and coordinate with other users before sending.
        Do not retry an uncertain write after a timeout or cancellation.
        """
        return await call(ctx, "send", {"port": port, "text": text, "encoding": encoding})

    @mcp.tool()
    async def send_bytes(port: str, base64_data: str, ctx: Context) -> dict:
        """Queue exact base64-encoded bytes and await the driver write; no automatic retry."""
        return await call(ctx, "send", {"port": port, "base64": base64_data})

    @mcp.tool()
    async def execute(port: str, text: str, expect: str, ctx: Context, timeout: float = 10,
                      wait_seconds: float = 30, newline: str = "\r", encoding: str = "utf-8") -> dict:
        """Queue a write, then observe SHARED RX independently until literal expect or timeout.

        A write may only activate an askfirst console, not execute the command; stale prompts and
        echoed input do not prove readiness. Enter can execute pending input; inspect context first.
        Other MCP/Telnet clients may write at any time; no command/response queue or isolation.
        Any client's output can satisfy expect, so a match is not proof your command completed.
        additional_writes_observed counts subsequent writes from any source during this call.
        Result is bounded to 64 KiB. No automatic Ctrl+C, retry or input gating after timeout.
        wait_seconds is deprecated and ignored (v0.1 compatibility); no input lease exists.
        """
        return await call(ctx, "execute", {"port": port, "text": text, "expect": expect,
                                           "timeout": timeout, "wait_seconds": wait_seconds,
                                           "newline": newline, "encoding": encoding})

    @mcp.tool()
    async def close_port(port: str, ctx: Context) -> dict:
        """Explicitly close idle unused port. Refuses Telnet clients or active execute calls.

        Passive MCP readers are not tracked as subscriptions: confirm nobody still needs capture first.
        """
        return await call(ctx, "close_port", {"port": port})

    return mcp


def main() -> None:
    create_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
