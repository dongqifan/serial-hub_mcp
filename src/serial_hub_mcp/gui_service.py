"""Attach the desktop controller to a shared daemon, starting an idle one if needed."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

from .cli import rpc
from .settings import api_url, state_directory


def _refused(exc: BaseException) -> bool:
    # httpx wraps httpcore, which in turn wraps the platform socket error.
    while exc is not None:
        if isinstance(exc, OSError) and (
            exc.errno in (errno.ECONNREFUSED, 10061) or getattr(exc, 'winerror', None) == 10061
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _probe() -> dict | None:
    try:
        data = rpc('list_ports', {})
    except httpx.ConnectError as exc:
        if _refused(exc):
            return None
        raise RuntimeError(f'无法连接 {api_url()}：{exc}') from exc
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise RuntimeError(f'{api_url()} 未返回有效串口清单，请检查服务或 API 端口：{exc}') from exc
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('available', 'open')):
        raise RuntimeError(f'{api_url()} 未返回 Serial Hub 的串口清单，请检查 API 端口。')
    return data


def _start(log: Path) -> subprocess.Popen:
    # No console and no pipes back to the GUI. Closing the window must not
    # interrupt capture or a terminal that connected through another client.
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as stream:
        return subprocess.Popen(
            [sys.executable, '-m', 'serial_hub_mcp.cli', 'serve'],
            cwd=Path(sys.executable).parent, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt',
        )


def ensure_service() -> dict:
    """Return live inventory. Only a refused connection warrants starting a daemon."""
    data = _probe()
    if data is not None:
        return data
    log = state_directory() / 'gui-daemon.log'
    try:
        child = _start(log)
    except OSError as exc:
        raise RuntimeError(f'无法启动 Serial Hub 服务：{exc}') from exc
    deadline = time.monotonic() + 8
    while True:
        # Probe first: a concurrent launch may already have won the API socket.
        data = _probe()
        if data is not None:
            return data
        code = child.poll()
        if code is not None:
            raise RuntimeError(f'服务未能启动（退出码 {code}）。请查看 {log}')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'等待 {api_url()} 就绪超时。请查看 {log}，然后刷新。')
        time.sleep(0.15)
