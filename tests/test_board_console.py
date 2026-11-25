"""Drive the board console buttons with simulated serial hardware and local profile storage."""
from copy import deepcopy
import json
import threading
import time
from unittest.mock import Mock

import pytest

tk = pytest.importorskip('tkinter')
from serial_hub_mcp import board_console as ui
from serial_hub_mcp import desktop_registration, gui_service
from serial_hub_mcp.boards import load_boards, save_boards


EMPTY = {'available': [], 'open': []}


def offline(*args):
    raise OSError('test service offline')


def settle(panel):
    timeout = time.monotonic() + 5
    while panel.busy and time.monotonic() < timeout:
        panel.root.update()
        time.sleep(.01)
    assert not panel.busy
    panel.root.update()


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    # Every possible service entry point is isolated, including startup and the
    # background heartbeat. No test here can probe a real daemon or enumerate hardware.
    monkeypatch.setattr(ui, 'rpc', offline)
    monkeypatch.setattr(ui, 'ensure_service', offline)
    monkeypatch.setattr(gui_service, 'rpc', offline)
    try:
        widget = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    clipboard = []
    monkeypatch.setattr(widget, 'clipboard_clear', clipboard.clear)
    monkeypatch.setattr(widget, 'clipboard_append', clipboard.append)
    monkeypatch.setattr(widget, 'clipboard_get', lambda: ''.join(clipboard))
    return widget


def test_board_console_selected_scope_and_global_release(root, monkeypatch):
    one = dict(ui.new_board(), name='主控', port='COM3', serial_user='console', web_user='ui-admin',
               web_url='https://board.example/login')
    two = dict(ui.new_board(), name='传感器', port='COM7')
    save_boards({'台架': [one, two]})
    opened = {'COM9': dict(port='COM9', config={'baudrate': 9600}, connected=True, telnet='127.0.0.1:4500')}
    calls = []

    def rpc(op, args):
        if op in ('console_report_tftp', 'console_forget_tftp'):
            return {}
        calls.append((op, args.get('port')))
        if op == 'list_ports':
            return dict(open=deepcopy(list(opened.values())), available=[{'port': 'COM3'}, {'port': 'COM7'}])
        name = args['port']
        if op == 'open_port':
            assert set(args) == {'port', 'config'}
            opened[name] = dict(port=name, config=args['config'], connected=True, telnet='127.0.0.1:4950')
            return deepcopy(opened[name])
        if op == 'close_port':
            if name == 'COM9':
                raise ValueError('terminal busy')
            del opened[name]
            return {'closed': name}
        pytest.fail(f'Unexpected RPC: {op}')

    monkeypatch.setattr(ui, 'rpc', rpc)
    monkeypatch.setattr(ui, 'ensure_service', lambda: rpc('list_ports', {}))
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        assert calls == [('list_ports', None)]
        assert panel.fields['name'].get() == '主控'
        assert panel.fields['web'].get() == 'https://board.example/login'
        assert panel.fields['tftp'].get() == '未配置'
        assert 'console' in panel.fields['serial_user'].get()
        panel.buttons['claim'].invoke()
        settle(panel)
        assert ('open_port', 'COM3') in calls and ('open_port', 'COM7') not in calls
        assert panel.fields['telnet'].get() == '127.0.0.1:4950'
        panel.buttons['copy'].invoke()
        settle(panel)
        assert '主控' in root.clipboard_get() and '传感器' not in root.clipboard_get()
        panel.buttons['group_claim'].invoke()
        settle(panel)
        assert calls.count(('open_port', 'COM3')) == 1 and ('open_port', 'COM7') in calls
        panel.buttons['group_copy'].invoke()
        settle(panel)
        assert '主控' in root.clipboard_get() and '传感器' in root.clipboard_get()
        prompts = []
        monkeypatch.setattr(ui.messagebox, 'askyesno', lambda title, text, **kw: prompts.append(text) or True)
        panel.buttons['release'].invoke()
        settle(panel)
        assert 'COM3' in prompts[-1] and 'COM7' not in prompts[-1]
        assert set(opened) == {'COM7', 'COM9'}
        panel.buttons['release_all'].invoke()
        settle(panel)
        assert 'COM7' in prompts[-1] and 'COM9' in prompts[-1]
        assert set(opened) == {'COM9'} and 'terminal busy' in panel.report
    finally:
        panel.close()


def test_board_management_still_works_offline(root, monkeypatch):
    rpc = Mock(side_effect=offline)
    monkeypatch.setattr(ui, 'rpc', rpc)
    names = iter(['新项目', '改名项目', '改名项目'])
    monkeypatch.setattr(ui.simpledialog, 'askstring', lambda *a, **kw: next(names))
    monkeypatch.setattr(ui, 'edit_board', lambda r, b, ports: dict(b, name='离线板', web_url='http://dut.local'))
    monkeypatch.setattr(ui.messagebox, 'askyesno', lambda *a, **kw: True)
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        assert not panel.known and panel.buttons['copy_id'].instate(['disabled'])
        panel.buttons['add_group'].invoke()
        panel.buttons['copy_id'].invoke()
        first = root.clipboard_get()
        assert first == panel.id_entry.get() == ui.load_catalog().projects[0].id
        assert panel.id_entry.instate(['readonly'])
        assert panel.buttons['group_copy'].instate(['disabled'])  # Empty project still has an ID.
        panel.buttons['add'].invoke()
        assert load_boards()['新项目'][0]['name'] == '离线板'
        assert panel.fields['web'].get() == 'http://dut.local'
        assert panel.buttons['claim'].instate(['disabled'])
        board_id = panel.selected_board()['id']
        panel.change_project('rename')
        assert panel.selected_board()['id'] == board_id
        panel.buttons['copy_id'].invoke()
        assert root.clipboard_get() == first and panel.picker.get() == '改名项目'
        panel.buttons['delete'].invoke()
        assert load_boards() == {'改名项目': []}
        panel.change_project('delete')
        assert panel.buttons['copy_id'].instate(['disabled'])
        panel.buttons['add_group'].invoke()
        panel.buttons['copy_id'].invoke()
        assert root.clipboard_get() == ui.load_catalog().projects[0].id != first
        assert all(c.args[0] == 'console_report_tftp' for c in rpc.call_args_list)
    finally:
        panel.close()


def test_local_tftp_manual_scope_confirmation_offline_handoff_and_shutdown(root, monkeypatch, tmp_path):
    from serial_hub_mcp.tftp_panel import ServicePanel
    from queue import Queue
    from types import SimpleNamespace
    import sys

    sent = Queue()
    def rpc(op, args):
        sent.put((op, deepcopy(args)))
        return offline()
    monkeypatch.setattr(ui, 'rpc', rpc)
    instances = []

    class Server:
        def __init__(self, ip, port, directory):
            self.running = False
            instances.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setitem(sys.modules, 'serial_hub_mcp.tftp_server', SimpleNamespace(TftpServer=Server))
    def cfg(name, port):
        return dict(name=name, ip='127.0.0.1', port=port, directory=str(tmp_path))
    a = dict(ui.new_board(), name='板甲', tftp_servers=[cfg('DSP', 1069), cfg('SoC', 2069)])
    b = dict(ui.new_board(), name='板乙', tftp_servers=[cfg('DSP', 3069)])
    save_boards({'台架': [a, b]})
    snapshots = []
    def hint(project, boards, inv, running_services):
        snapshots.append(([v['name'] for v in boards], inv, running_services))
        return 'board details'
    monkeypatch.setattr(ui, 'board_hint', hint)
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        window = panel.reporter.window_id
        assert sent.get(timeout=2) == ('console_report_tftp', {'window_id': window, 'services': []})
        root.after_cancel(panel.heartbeat_id)
        panel.heartbeat()
        assert sent.get(timeout=2) == ('console_report_tftp', {'window_id': window, 'services': []})
        dialog = ServicePanel(root, a, panel.services)
        expected_key = (a['id'], 'DSP')
        assert not instances and panel.buttons['services'].instate(['!disabled'])
        monkeypatch.setattr(ui.messagebox, 'askyesno', lambda *a, **k: False)
        dialog.start_button.invoke()
        assert not instances
        prompts = []
        monkeypatch.setattr(ui.messagebox, 'askyesno', lambda title, text, **k: prompts.append(text) or True)
        dialog.start_button.invoke()
        assert instances[0].running and all(s in prompts[0] for s in ('127.0.0.1', str(tmp_path), '1069', '没有身份验证'))
        assert dialog.rows.set('0', 'state') == '运行中' and dialog.rows.set('1', 'state') == '未启动'
        assert sent.get(timeout=2) == ('console_report_tftp', {'window_id': window, 'services': [
            {'board_id': a['id'], **a['tftp_servers'][0]}]})
        dialog.rows.selection_set('1'); root.update(); dialog.start_button.invoke()
        assert panel.services.active([a]) == {expected_key, (a['id'], 'SoC')}
        dialog.stop_button.invoke(); assert not instances[1].running and instances[0].running
        dialog.destroy()
        panel.details()
        assert panel.fields['tftp'].get() == '已配置 2 个，本窗口运行 1 个'
        assert panel.buttons['edit'].instate(['disabled'])
        panel.buttons['copy'].invoke(); settle(panel)
        assert snapshots[-1] == (['板甲'], None, {expected_key})
        panel.listing.selection_set(b['id']); root.update()
        panel.buttons['copy'].invoke(); settle(panel)
        assert snapshots[-1] == (['板乙'], None, set())
        panel.services.start(b['id'], b['tftp_servers'][0])
        panel.buttons['group_copy'].invoke(); settle(panel)
        assert snapshots[-1] == (['板甲', '板乙'], None, {expected_key, (b['id'], 'DSP')})
        save_boards({'台架': [b]})  # Another window removed the running board.
        panel.refresh(); settle(panel)
        assert not instances[0].running and instances[2].running
        assert panel.selected_board()['id'] == b['id']
    finally:
        panel.close()
    assert not any(s.running for s in instances)
    while True:
        op, args = sent.get(timeout=2)
        if op == 'console_forget_tftp':
            assert args == {'window_id': window}
            break


def test_outer_start_only_pending_for_selected_board(root, tmp_path, monkeypatch):
    def config(n):
        return dict(name=f'SoC{n}', ip='127.0.0.1', port=1069+n, directory=str(tmp_path))
    a = dict(ui.new_board(), name='板A', tftp_servers=[config(i) for i in range(3)])
    b = dict(ui.new_board(), name='板B', tftp_servers=[config(3)])
    save_boards({'实验室': [a, b]})
    monkeypatch.setattr(ui, 'ensure_service', lambda: dict(available=[], open=[]))
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        active = {(a['id'], 'SoC0')}
        calls, prompts = [], []
        def start(key, cfg):
            calls.append((key, cfg['name']))
            if cfg['name'] == 'SoC1':
                raise OSError('port busy')
            active.add((key, cfg['name']))
        monkeypatch.setattr(panel.services, 'active', lambda boards: {k for k in active if k[0] in {x['id'] for x in boards}})
        monkeypatch.setattr(panel.services, 'start', start)
        answers = iter([False, True])
        monkeypatch.setattr(ui.messagebox, 'askyesno', lambda title, text, **kw: (prompts.append(text), next(answers))[1])
        panel.buttons['start_tftp'].invoke()
        assert not calls
        panel.buttons['start_tftp'].invoke()
        assert len(prompts) == 2 and 'SoC1' in prompts[0] and 'SoC0' not in prompts[0] and 'SoC3' not in prompts[0]
        assert calls == [(a['id'], 'SoC1'), (a['id'], 'SoC2')]
        assert 'port busy' in panel.report and '运行 2 个' in panel.fields['tftp'].get()
    finally:
        panel.close()


def test_conflicting_catalog_edit_reloads_project_and_board_by_id(root, monkeypatch):
    a, b = dict(ui.new_board(), name='first'), dict(ui.new_board(), name='selected')
    save_boards({'other': [], 'bench': [a, b]})
    warnings = Mock()
    monkeypatch.setattr(ui.messagebox, 'showwarning', warnings)
    monkeypatch.setattr(ui.simpledialog, 'askstring', lambda *a, **kw: 'stale edit')
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        panel.picker.current(next(i for i, p in enumerate(panel.projects) if p.name == 'bench'))
        panel.pick_project()
        panel.listing.selection_set(b['id'])
        ident = panel.project_id
        changed = ui.load_catalog()
        project = ui.Project(ident, 'external edit', [a, {**b, 'name': 'externally changed'}])
        ui.save_catalog([project, *(p for p in changed.projects if p.id != ident)], expected_revision=changed.revision)
        panel.change_project('rename')
        warnings.assert_called_once()
        assert panel.id_entry.get() == ident and panel.picker.get() == 'external edit'
        assert panel.selected_board()['id'] == b['id'] and panel.fields['name'].get() == 'externally changed'
        assert panel.catalog.revision == ui.load_catalog().revision

        panel.change_project('rename')  # A fresh, explicit edit now uses the new revision.
        assert panel.active_project().name == 'stale edit' and panel.project_id == ident
    finally:
        panel.close()


def test_mcp_panel_uses_reported_identity_and_does_not_restart_after_stop(root, monkeypatch):
    tk_thread = threading.get_ident()
    threads, calls = [], []
    service = dict(name='test serial hub', version='5.0-test', instance_id='instance-1',
                   capabilities=['projects', 'idle_stop'], open_ports=['COM9'])
    running = False
    def start():
        nonlocal running
        threads.append(threading.get_ident())
        running = True
        return EMPTY
    ensure = Mock(side_effect=start)
    def rpc(op, args):
        nonlocal running
        threads.append(threading.get_ident())
        calls.append((op, deepcopy(args)))
        if not running:
            return offline()
        if op == 'console_status':
            return deepcopy(service)
        if op == 'list_ports':
            return EMPTY
        if op == 'console_stop':
            if args['instance_id'] != service['instance_id']:
                raise ValueError('instance changed')
            running = False
            return {'stopping': True}
        assert op in ('console_report_tftp', 'console_forget_tftp')
        return {}
    monkeypatch.setattr(ui, 'rpc', rpc)
    monkeypatch.setattr(ui, 'ensure_service', ensure)
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        panel.buttons['mcp'].invoke(); settle(panel)
        dialog = panel.mcp_panel
        assert all(s in dialog.state.get() for s in ('test serial hub', '5.0-test', 'instance-1', 'idle_stop', 'COM9'))
        assert dialog.stop_button.instate(['disabled'])
        service['open_ports'] = []
        dialog.query_button.invoke(); settle(panel)
        assert dialog.stop_button.instate(['!disabled'])
        prompts = Mock(side_effect=[False, True, True])
        monkeypatch.setattr(ui.messagebox, 'askyesno', prompts)
        dialog.stop_button.invoke()
        assert prompts.call_args.kwargs['default'] == 'no' and 'instance-1' in prompts.call_args.args[1]
        assert not any(op == 'console_stop' for op, args in calls)
        service['instance_id'] = 'instance-2'  # Replaced after the user saw the status.
        dialog.stop_button.invoke(); settle(panel)
        assert ('console_stop', {'instance_id': 'instance-1'}) in calls
        assert 'instance changed' in dialog.notice.get() and running and dialog.stop_button.instate(['disabled'])
        dialog.query_button.invoke(); settle(panel)
        dialog.stop_button.invoke()
        dialog.dismiss()  # Closing the panel must not lose the stop result.
        settle(panel)
        assert ('console_stop', {'instance_id': 'instance-2'}) in calls
        assert not running and not panel.known
        panel.buttons['refresh'].invoke(); settle(panel)
        panel.show_mcp(); settle(panel)
        assert '不可用' in dialog.state.get() and not running
        ensure.assert_called_once_with()  # Startup only. Queries, refreshes, and reports never spawn.
        dialog.start_button.invoke(); settle(panel)
        assert running and panel.known and ensure.call_count == 2 and 'instance-2' in dialog.state.get()
        assert tk_thread not in threads
    finally:
        panel.close()


def test_legacy_service_and_explicit_client_config_work_without_control_rpc(root, monkeypatch, tmp_path):
    from tkinter import filedialog
    path = tmp_path / desktop_registration.CONFIG_NAME
    monkeypatch.setenv('SERIAL_HUB_API_PORT', '9876')
    original = b'{"other": true, "mcpServers": {"keep": {}}}'
    path.write_bytes(original)
    save_boards({'offline project': []})
    calls = []
    def legacy(op, args):
        calls.append(op)
        if op == 'list_ports':
            return EMPTY
        raise ValueError("'port'")  # A pre-console_status daemon's unknown operation response.
    monkeypatch.setattr(ui, 'rpc', legacy)
    monkeypatch.setattr(ui, 'ensure_service', lambda: EMPTY)
    choices = Mock(side_effect=['', str(path), str(path)])
    monkeypatch.setattr(filedialog, 'asksaveasfilename', choices)
    confirms = Mock(side_effect=[False, True])
    monkeypatch.setattr(ui.messagebox, 'askyesno', confirms)
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        panel.buttons['mcp'].invoke(); settle(panel)
        dialog = panel.mcp_panel
        assert '旧版本' in dialog.state.get() and dialog.stop_button.instate(['disabled'])
        assert panel.known and panel.buttons['copy_id'].instate(['!disabled'])
        assert choices.call_count == 0
        dialog.copy_config()
        entry = json.loads(root.clipboard_get())['mcpServers']['serial_hub']
        assert entry == {'command': str(desktop_registration.stdio_python()),
                         'args': ['-I', '-m', 'serial_hub_mcp.mcp_server'], 'env': {'SERIAL_HUB_API_PORT': '9876'}}
        dialog.merge_button.invoke()
        confirms.assert_not_called()
        dialog.merge_button.invoke()
        assert path.read_bytes() == original and not list(tmp_path.glob('*.bak'))
        dialog.merge_button.invoke(); settle(panel)
        assert json.loads(path.read_bytes()) == {'other': True, 'mcpServers': {'keep': {}, 'serial_hub': entry}}
        backup, = tmp_path.glob('*.bak')
        assert backup.read_bytes() == original and str(backup) in dialog.notice.get()
        assert 'console_stop' not in calls
    finally:
        panel.close()
