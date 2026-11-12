from __future__ import annotations

import os
from pathlib import Path


def state_directory() -> Path:
    override = os.environ.get("SERIAL_HUB_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "state"))
    return base / "serial-hub-mcp"


def api_url() -> str:
    # No authentication: this service deliberately stays on loopback.
    port = int(os.environ.get("SERIAL_HUB_API_PORT", "8766"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SERIAL_HUB_API_PORT")
    return f"http://127.0.0.1:{port}"
