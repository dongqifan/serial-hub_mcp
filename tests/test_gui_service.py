import errno
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
