"""Attach the desktop controller to a shared daemon, starting an idle one if needed."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import httpx

from .cli import rpc
from .core import PortConfig
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


def inventory_snapshot(call) -> dict:
    """Read-only; reject unrelated or malformed replies before Tk renders them."""
    data = call('list_ports', {})
    invalid = f'{api_url()} 未返回有效的 Serial Hub 串口清单，请检查 API 端口。'
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('available', 'open')):
        raise ValueError(invalid)
    for item in (*data['open'], *data['available']):
        if not isinstance(item, dict) or not isinstance(item.get('port'), str):
            raise ValueError(invalid)
    for item in data['open']:
        if type(item.get('connected')) is not bool or not isinstance(item.get('config'), dict):
            raise ValueError(invalid)
        try:
            PortConfig(**item['config'])
        except TypeError:
            raise ValueError(invalid) from None
    return data


def _probe() -> dict | None:
    try:
        return inventory_snapshot(rpc)
    except httpx.ConnectError as exc:
        if _refused(exc):
            return None
        raise RuntimeError(f'无法连接 {api_url()}：{exc}') from exc
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise RuntimeError(f'{api_url()} 未返回有效串口清单，请检查服务或 API 端口：{exc}') from exc


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


def console_status(call) -> dict:
    """Observe, never launch. A legacy inventory alone isn't a service identity."""
    unsupported = '服务未提供控制台状态（可能是旧版本）。请更新并手动重启服务；不会强制停止进程。'
    try:
        data = call('console_status', {})
    except (ValueError, AttributeError) as exc:
        raise ValueError(unsupported) from exc
    if (not isinstance(data, dict)
            or not all(isinstance(data.get(k), str) and data[k] for k in ('name', 'version', 'instance_id'))
            or not all(isinstance(data.get(k), list) and all(isinstance(item, str) for item in data[k])
                       for k in ('capabilities', 'open_ports'))
            or type(data.get('stopping', False)) is not bool):
        raise ValueError(unsupported)
    return data


class TftpReporter:
    """Send one window's latest observation, never queue a history of stale heartbeats."""
    def __init__(self, call):
        self.window_id = uuid.uuid4().hex
        self._call = call
        self._condition = threading.Condition()
        self._latest = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name='Console TFTP reports', daemon=True)
        self._thread.start()

    def publish(self, services):
        # The GUI supplies a detached snapshot; the worker never touches Tk or a listener.
        with self._condition:
            if not self._closing:
                self._latest = services
                self._condition.notify()

    def close(self):
        # Forget after any in-flight report. Don't hold window shutdown up for an
        # unreachable daemon: this withdrawal is best effort, and reports expire.
        with self._condition:
            self._closing = True
            self._latest = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._latest is not None)
                closing = self._closing
                services, self._latest = self._latest, None
            args = {'window_id': self.window_id}
            try:
                if closing:
                    self._call('console_forget_tftp', args)
                else:
                    self._call('console_report_tftp', {**args, 'services': services})
            except (OSError, httpx.HTTPError, ValueError, AttributeError):
                pass  # Offline and old daemons don't affect GUI-owned listeners.
            if closing:
                return
