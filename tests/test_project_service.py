import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from serial_hub_mcp import boards
from serial_hub_mcp.core import HubError, PortConfig
from serial_hub_mcp.project_service import ProjectService, WindowReports


@pytest.fixture
def bench(rig, tmp_path, monkeypatch):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr('serial_hub_mcp.core.list_ports.comports', lambda: [
        SimpleNamespace(device='COM3', description='simulated', hwid='fake'),
    ])
    hub, devices = rig
    service = ProjectService(hub, Mock())
    a, b = boards.new_project('Alpha'), boards.new_project('Beta')
    cfg = dict(name='firmware', ip='127.0.0.1', port=1069, directory=str(tmp_path / 'fw'))
    cipher = base64.b64encode(bytes.fromhex('01000000d08c9ddf0115d1118c7a00c04fc297eb') + b'fixture').decode()
    a.boards = [dict(boards.new_board(), name='Master', port='COM3', web_url='https://board.example',
                     ai_prompt='Only observe this fixture.', serial_user='private-serial-user',
                     web_user='private-web-user', copy_login=True, web_secret=cipher,
                     tftp_servers=[cfg]), dict(boards.new_board(), name='Offline')]
    b.boards = [dict(boards.new_board(), name='Other board', port='COM3', tftp_servers=[cfg.copy()])]
    boards.save_catalog([a, b], expected_revision=boards.load_catalog().revision)
    return service, devices, a, b


async def describe(service, project):
    return await service.board_rpc('get_project', {'project_id': project.id}, 'agent')


async def test_discovery_is_scoped_read_only_and_does_not_decrypt_credentials(bench, monkeypatch):
    service, devices, a, b = bench
    monkeypatch.setattr('serial_hub_mcp.board_secrets.reveal', lambda _: pytest.fail('discovery decrypted a secret'))
    result = await describe(service, a)
    assert result['project_id'] == a.id and result['name'] == 'Alpha'
    master, offline = result['boards']
    assert master['serial']['state'] == 'available' and master['serial']['connection_id'] is None
    assert master['web_url'] == 'https://board.example' and master['operator_note'] == a.boards[0]['ai_prompt']
    assert offline['serial']['state'] == 'unbound' and offline['serial']['binding_id'] is None
    text = json.dumps(result)
    for excluded in (a.boards[0]['web_secret'], 'private-serial-user', 'private-web-user', 'serial_secret',
                     'web_secret', 'copy_login', b.id, b.boards[0]['id']):
        assert excluded not in text
    with pytest.raises(HubError, match='Unknown project'):
        await service.board_rpc('get_project', {'project_id': '0' * 32}, 'agent')
    assert not devices


async def test_inventory_failure_does_not_hide_offline_project_resources(bench, monkeypatch):
    service, devices, a, _ = bench
    monkeypatch.setattr('serial_hub_mcp.core.list_ports.comports', Mock(side_effect=OSError('unavailable')))
    master, offline = (await describe(service, a))['boards']
    assert master['serial']['state'] == 'unknown' and master['web_url'] == 'https://board.example'
    assert offline['serial']['state'] == 'unbound' and not devices


async def test_serial_handles_are_scoped_and_reject_stale_bindings(bench):
    service, devices, a, b = bench
    serial = (await describe(service, a))['boards'][0]['serial']
    ids = {'project_id': a.id, 'board_id': a.boards[0]['id']}
    b_ids = {'project_id': b.id, 'board_id': b.boards[0]['id']}
    open_args = {**ids, 'binding_id': serial['binding_id']}
    with pytest.raises(HubError, match='does not belong'):
        await service.board_rpc('open_board_serial', {**open_args, 'project_id': b.id}, 'agent')
    assert not devices
    opened = await service.board_rpc('open_board_serial', open_args, 'agent')
    handle = {**ids, 'connection_id': opened['connection_id']}
    dev = devices['COM3']
    assert dev.writes == [] and 'log_path' not in opened
    assert (await describe(service, a))['boards'][0]['serial']['connection_id'] == opened['connection_id']
    with pytest.raises(HubError, match='Stale'):
        await service.board_rpc('send_board_text', {**b_ids, 'connection_id': opened['connection_id'], 'text': 'NO'}, 'agent')
    with pytest.raises(HubError, match='saved port'):
        await service.board_rpc('send_board_text', {**handle, 'port': 'COM8', 'text': 'NO'}, 'agent')
    assert not dev.writes
    assert (await service.board_rpc('send_board_text', {**handle, 'text': 'x'}, 'agent'))['written'] == 1
    await service.board_rpc('send_board_bytes', {**handle, 'base64_data': '/wA='}, 'agent')
    assert dev.writes == [b'x', b'\xff\x00']
    dev.responder = lambda _: dev.input.put(b'reply\r\n# ')
    result = await service.board_rpc('execute_board', {**handle, 'text': 'q', 'expect': '\r\n# '}, 'agent')
    assert result['outcome'] == 'matched' and result['shared_output'] is True
    rx = await service.board_rpc('read_board_output', {**handle, 'cursor': opened['cursor_end']}, 'other')
    assert rx['text'] == 'reply\r\n# '
    await service.board_rpc('close_board_serial', handle, 'agent')
    reopened = await service.board_rpc('open_board_serial', open_args, 'agent')
    assert reopened['connection_id'] != opened['connection_id']
    with pytest.raises(HubError, match='Stale'):
        await service.board_rpc('close_board_serial', handle, 'agent')
    assert not devices['COM3'].closed
    current = boards.load_catalog()
    current.by_id(a.id).boards[0]['port'] = 'COM8'
    boards.save_catalog(current.projects, expected_revision=current.revision)
    with pytest.raises(HubError, match='binding changed'):
        await service.board_rpc('open_board_serial', open_args, 'agent')
    with pytest.raises(HubError):
        await service.board_rpc('send_board_text', {**ids, 'connection_id': reopened['connection_id'], 'text': 'NO'}, 'agent')
    assert set(devices) == {'COM3'} and devices['COM3'].writes == []


async def test_disconnected_board_can_drain_and_release_but_not_write(bench):
    service, devices, a, _ = bench
    ids = {'project_id': a.id, 'board_id': a.boards[0]['id']}
    port = await service.hub.open('COM3', PortConfig())
    device = devices['COM3']
    device.input.put(b'final bytes')
    device.input.put(OSError('unplugged'))
    serial = service.hub.get('COM3')
    async with asyncio.timeout(2):
        await asyncio.shield(serial.reader)
    info = (await describe(service, a))['boards'][0]['serial']
    assert info['state'] == 'disconnected' and info['connected'] is False
    handle = {**ids, 'connection_id': info['connection_id']}
    assert (await service.board_rpc('read_board_output', {**handle, 'cursor': port['cursor_end']}, 'test'))['text'] == 'final bytes'
    with pytest.raises(HubError, match='disconnected'):
        await service.board_rpc('send_board_text', {**handle, 'text': 'NO'}, 'test')
    await service.board_rpc('close_board_serial', handle, 'test')
    assert device.closed and not device.writes


async def test_tftp_state_is_ephemeral_and_matches_full_board_configuration(bench):
    service, devices, a, b = bench
    clock = [100.0]
    service.tftp = WindowReports(lambda: clock[0])
    window = 'a' * 32
    cfg = a.boards[0]['tftp_servers'][0]
    report = {'window_id': window, 'services': [{**cfg, 'board_id': a.boards[0]['id']}]}

    async def listener(project):
        return (await describe(service, project))['boards'][0]['tftp_servers'][0]['state']

    assert await listener(a) == 'unknown'
    await service.management('console_report_tftp', report)
    assert await listener(a) == 'reported_running'
    assert await listener(b) == 'not_reported_running'
    report['services'][0]['directory'] += '-old'
    await service.management('console_report_tftp', report)
    assert await listener(a) == 'not_reported_running'
    clock[0] += 16
    assert await listener(a) == 'unknown'
    await service.management('console_report_tftp', report)
    await service.management('console_forget_tftp', {'window_id': window})
    assert await listener(a) == 'unknown' and not devices


async def test_stop_is_instance_scoped_and_will_not_interrupt_a_port_or_listener(bench):
    service, _, a, _ = bench
    status = await service.management('console_status', {})
    assert 'idle_stop' in status['capabilities'] and not status['open_ports']
    args = {'instance_id': status['instance_id']}
    with pytest.raises(HubError):
        await service.management('console_stop', {'instance_id': 'other'})
    await service.hub.open('COM8', PortConfig())
    with pytest.raises(HubError, match='Release'):
        await service.management('console_stop', args)
    await service.hub.close_port('COM8', 'test')
    window = 'b' * 32
    service.tftp.report(window, [dict(a.boards[0]['tftp_servers'][0], board_id=a.boards[0]['id'])])
    with pytest.raises(HubError, match='TFTP'):
        await service.management('console_stop', args)
    service.request_stop.assert_not_called()
    await service.management('console_forget_tftp', {'window_id': window})
    assert await service.management('console_stop', args) == {'stopping': True}
    service.request_stop.assert_called_once_with()
    with pytest.raises(HubError, match='stopping'):
        await service.hub.open('COM8', PortConfig())
