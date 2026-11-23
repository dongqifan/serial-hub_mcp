"""Drive the board console buttons with simulated serial hardware and local profile storage."""
from copy import deepcopy
import time

import pytest

tk = pytest.importorskip('tkinter')
from serial_hub_mcp import board_console as ui


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
    try:
        widget = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    return widget


def test_board_console_selected_scope_and_global_release(root, monkeypatch):
    one = dict(ui.new_board(), name='主控', port='COM3', serial_user='console', web_user='ui-admin',
               web_url='https://board.example/login', tftp_targets=[dict(soc='主芯片', host='2001:db8::1', port=1069),
                                                                  dict(soc='协处理器', host='fw.test', port=69)])
    two = dict(ui.new_board(), name='传感器', port='COM7')
    ui.save_boards({'台架': [one, two]})
    opened = {'COM9': dict(port='COM9', config={'baudrate': 9600}, connected=True, telnet='127.0.0.1:4500')}
    calls = []

    def rpc(op, args):
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
        assert panel.tftp_display.get('1.0', 'end-1c') == '主芯片：[2001:db8::1]:1069\n协处理器：fw.test:69'
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
    def offline():
        raise OSError('offline')
    monkeypatch.setattr(ui, 'ensure_service', offline)
    monkeypatch.setattr(ui, 'rpc', lambda *args: pytest.fail('offline editing must not call RPC'))
    monkeypatch.setattr(ui.simpledialog, 'askstring', lambda *a, **kw: '新项目')
    monkeypatch.setattr(ui, 'edit_board', lambda r, b, ports: dict(b, name='离线板', web_url='http://dut.local'))
    monkeypatch.setattr(ui.messagebox, 'askyesno', lambda *a, **kw: True)
    panel = ui.BoardConsole(root)
    try:
        settle(panel)
        assert not panel.known
        panel.buttons['add_group'].invoke()
        panel.buttons['add'].invoke()
        assert ui.load_boards()['新项目'][0]['name'] == '离线板'
        assert panel.fields['web'].get() == 'http://dut.local'
        assert panel.buttons['claim'].instate(['disabled'])
        panel.buttons['delete'].invoke()
        assert ui.load_boards() == {'新项目': []}
    finally:
        panel.close()
