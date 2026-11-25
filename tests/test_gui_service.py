import errno
import re
import threading
from unittest.mock import Mock, call

import httpx
import pytest

from serial_hub_mcp import gui_service as service


INVENTORY = {"available": [], "open": []}


def refused():
    exc = httpx.ConnectError('connect failed')
    exc.__cause__ = ConnectionRefusedError(errno.ECONNREFUSED, 'refused')
    return exc


def test_reuse_does_not_touch_hardware_or_spawn(monkeypatch):
    probe = Mock(return_value=INVENTORY)
    monkeypatch.setattr(service, 'rpc', probe)
    monkeypatch.setattr(service, '_start', Mock(side_effect=AssertionError))
    assert service.ensure_service() == INVENTORY
    probe.assert_called_once_with('list_ports', {})


def test_starts_daemon_without_port_and_waits(monkeypatch, tmp_path):
    probe = Mock(side_effect=[refused(), refused(), INVENTORY])
    monkeypatch.setattr(service, 'rpc', probe)
    monkeypatch.setattr(service, 'state_directory', lambda: tmp_path)
    monkeypatch.setattr(service.time, 'sleep', lambda _: None)
    popen = Mock(return_value=Mock(poll=lambda: None))
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    assert service.ensure_service() == INVENTORY
    popen.assert_called_once()
    assert popen.call_args.args == ([service.sys.executable, '-m', 'serial_hub_mcp.cli', 'serve'],)
    assert probe.call_args_list == [call('list_ports', {})] * 3
    assert (tmp_path / 'gui-daemon.log').is_file()


@pytest.mark.parametrize('response', [ValueError('not JSON'), {'open': 1}, httpx.ReadTimeout('slow'),
                                      httpx.ConnectError('permission denied')])
def test_does_not_spawn_on_conflict_or_timeout(monkeypatch, response):
    probe = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(service, 'rpc', probe)
    monkeypatch.setattr(service, '_start', Mock(side_effect=AssertionError))
    with pytest.raises(RuntimeError):
        service.ensure_service()


@pytest.mark.parametrize(('exit_code', 'message'), [(1, '退出码'), (None, '超时')])
def test_failed_or_slow_start_is_bounded(monkeypatch, exit_code, message):
    monkeypatch.setattr(service, 'rpc', Mock(side_effect=refused()))
    monkeypatch.setattr(service, '_start', Mock(return_value=Mock(poll=lambda: exit_code)))
    monkeypatch.setattr(service.time, 'monotonic', Mock(side_effect=[0, 9]))
    with pytest.raises(RuntimeError, match=f'{message}.*gui-daemon.log'):
        service.ensure_service()


@pytest.mark.parametrize('bad', [None, INVENTORY, {'name': 'old'}, ValueError("'port'")])
def test_console_identity_never_guessed_from_inventory(bad):
    call_rpc = Mock(side_effect=bad) if isinstance(bad, Exception) else Mock(return_value=bad)
    with pytest.raises(ValueError, match='旧版本'):
        service.console_status(call_rpc)
    call_rpc.assert_called_once_with('console_status', {})


def test_tftp_reporter_only_sends_latest_and_forgets_after_inflight_report():
    first_started, second_started, forgotten = (threading.Event() for _ in range(3))
    first_release, second_release = threading.Event(), threading.Event()
    calls = []
    def rpc(op, args):
        calls.append((op, args))
        if op == 'console_forget_tftp':
            forgotten.set()
            return {}
        started, release = (first_started, first_release) if len(calls) == 1 else (second_started, second_release)
        started.set()
        release.wait(timeout=3)
        raise httpx.ReadTimeout('simulated unavailable service')

    reporter = service.TftpReporter(rpc)
    assert re.fullmatch('[0-9a-f]{32}', reporter.window_id)
    final = [{'board_id': 'a'*32, 'name': 'DSP', 'ip': '127.0.0.1', 'port': 1069, 'directory': 'D:/example'}]
    try:
        reporter.publish([])
        assert first_started.wait(2)
        for i in range(10):
            reporter.publish([{**final[0], 'port': 1070+i}])
        reporter.publish(final)
        first_release.set()
        assert second_started.wait(2)
        reporter.publish([])
        reporter.close()  # In flight: no Tk wait, no pending report after the forget.
        reporter.publish(final)
    finally:
        reporter.close()
        first_release.set()
        second_release.set()
    assert forgotten.wait(2)
    assert calls == [('console_report_tftp', {'window_id': reporter.window_id, 'services': []}),
                     ('console_report_tftp', {'window_id': reporter.window_id, 'services': final}),
                     ('console_forget_tftp', {'window_id': reporter.window_id})]
