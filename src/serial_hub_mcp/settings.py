from __future__ import annotations

import os
from pathlib import Path
import secrets


def state_directory() -> Path:
    override = os.environ.get("SERIAL_HUB_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "state"))
    return base / "serial-hub-mcp"


def token_file() -> Path:
    return state_directory() / "access.token"


def load_token(*, create: bool = False) -> str:
    path = token_file()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation: a second daemon must never rotate the secret.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(32))
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise RuntimeError(f"Invalid token file: {path}")
    return token


def api_url() -> str:
    # Deliberately loopback-only: the token must not be sent to a remote host.
    port = int(os.environ.get("SERIAL_HUB_API_PORT", "8766"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SERIAL_HUB_API_PORT")
    return f"http://127.0.0.1:{port}"
