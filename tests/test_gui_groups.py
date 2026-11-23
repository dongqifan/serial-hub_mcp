"""End-to-end controller wiring: project presets, clipboard and global release scope."""
from copy import deepcopy
import time

import pytest

tk = pytest.importorskip('tkinter')
from serial_hub_mcp import gui


def settle(panel):
    deadline = time.monotonic() + 5
    while panel.busy and time.monotonic() < deadline:
        panel.root.update()
        time.sleep(.01)
    assert not panel.busy
    panel.root.update()


def test_project_buttons_and_global_release(monkeypatch, tmp_path):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    opened = {'COM11': dict(port='COM11', connected=True, config={'baudrate': 9600},
                            terminal_clients=1, active_executions=0, telnet='127.0.0.1:2311')}
    calls = []

    def rpc(operation, args):
        calls.append((operation, args.get('port')))
        if operation == 'list_ports':
            return {'available': [{'port': 'COM7'}, {'port': 'COM11'}], 'open': deepcopy(list(opened.values()))}
        port = args['port']
        if operation == 'open_port':
            opened[port] = dict(port=port, connected=True, config=args['config'], terminal_clients=0,
                                active_executions=0, telnet='127.0.0.1:2400')
            return deepcopy(opened[port])
        if operation == 'close_port':
            if opened[port]['terminal_clients']:
                raise ValueError('terminal busy')
            del opened[port]
            return {'closed': port}
        raise AssertionError(f'Unexpected operation: {operation}')

    project = {'Bench': [{'port': 'COM7', 'baudrate': 38400, 'role': '主控'}]}
    monkeypatch.setattr(gui, 'edit_project', lambda *args: (project, 'Bench'))
    monkeypatch.setattr(gui, 'rpc', rpc)
    monkeypatch.setattr(gui, 'ensure_service', lambda: rpc('list_ports', {}))
    panel = gui.Console(root)
    try:
        settle(panel)
        panel.new.invoke()
        assert gui.load_projects() == project
        for name in (gui.ALL_PORTS, 'Bench'):
            panel.picker.set(name)
            panel.picker.event_generate('<<ComboboxSelected>>')
            root.update()
        assert calls == [('list_ports', None)]
        assert panel.table.get_children() == ('COM7',)
        panel.group_open.invoke()
        settle(panel)
        assert [name for op, name in calls if op == 'open_port'] == ['COM7']
        assert opened['COM7']['config']['baudrate'] == 38400
        assert panel.table.set('COM7', 'endpoint') == '127.0.0.1:2400'
        opened['COM7']['telnet'] = '127.0.0.1:2543'  # Endpoint changed since the batch.
        panel.group_copy.invoke()
        settle(panel)
        hint = root.clipboard_get()
        assert all(s in hint for s in ('Bench', '主控', 'COM7', '38400', '127.0.0.1:2543'))
        assert 'COM11' not in hint and ':2400' not in hint
        prompts = []

        def confirm(title, text, **kwargs):
            prompts.append(text)
            return len(prompts) == 2

        monkeypatch.setattr(gui.messagebox, 'askyesno', confirm)
        panel.all_release.invoke()
        settle(panel)
        assert not any(op == 'close_port' for op, _ in calls)
        panel.all_release.invoke()
        settle(panel)
        assert len(prompts) == 2 and all('COM7' in s and 'COM11' in s for s in prompts)
        assert sorted(name for op, name in calls if op == 'close_port') == ['COM11', 'COM7']
        assert set(opened) == {'COM11'}
        assert 'COM7' in panel.report and 'COM11' in panel.report and 'terminal busy' in panel.report
        assert panel.show_report.instate(['!disabled'])
        assert panel.table.set('COM7', 'endpoint') == '—'
    finally:
        panel.close()
