import pytest

from serial_hub_mcp import projects


def test_projects_roundtrip_and_shared_ports(monkeypatch, tmp_path):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path / 'state'))
    assert projects.load_projects() == {}
    a = {'port': ' com3 ', 'baudrate': 115200, 'role': ' 主控 '}
    b = {'port': 'COM3', 'baudrate': 9600}
    projects.save_projects({' 联调 ': [a], '产测': [b]})
    assert projects.load_projects() == {
        '联调': [{'port': 'COM3', 'baudrate': 115200, 'role': '主控'}],
        '产测': [{'port': 'COM3', 'baudrate': 9600, 'role': ''}],
    }


@pytest.mark.parametrize('bad', [
    {'全部串口': []}, {'  ': []}, {'A': [{'port': 'COM0', 'baudrate': 115200}]},
    {'A': [{'port': 'COM1', 'baudrate': 0}]}, {'A': [{'port': 'COM1', 'baudrate': False}]},
    {'A': [{'port': 'COM1', 'baudrate': 9600}, {'port': 'com1', 'baudrate': 115200}]},
])
def test_invalid_save_preserves_data(monkeypatch, tmp_path, bad):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    projects.save_projects({'原项目': []})
    with pytest.raises(ValueError):
        projects.save_projects(bad)
    assert projects.load_projects() == {'原项目': []}


@pytest.mark.parametrize('broken', [b'broken JSON', b'{"a":[],"a":[]}', b'{"x":[{}]}', b'\xff'])
def test_corrupted_file_is_not_overwritten(monkeypatch, tmp_path, broken):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    path = tmp_path / 'projects.json'
    path.write_bytes(broken)
    with pytest.raises(ValueError, match='projects.json'):
        projects.load_projects()
    with pytest.raises(ValueError):
        projects.save_projects({})
    assert path.read_bytes() == broken


def test_atomic_failure_keeps_file_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    projects.save_projects({'原项目': []})

    def fail(*args):
        raise PermissionError('locked')

    monkeypatch.setattr(projects.os, 'replace', fail)
    with pytest.raises(PermissionError):
        projects.save_projects({})
    assert projects.load_projects() == {'原项目': []}
    assert sorted(p.name for p in tmp_path.iterdir()) == ['projects.json']


def test_editor_save_rename_delete_and_cancel(monkeypatch):
    tk = pytest.importorskip('tkinter')
    from serial_hub_mcp import project_editor as editor

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    try:
        original = {'已有': [{'port': 'COM3', 'baudrate': 9600, 'role': '控制器'}]}
        dlg = editor.ProjectEditor(root, original, None, ['COM3'])
        dlg.name.set('新项目')
        dlg.rows[0][0].set('com3')
        dlg.rows[0][2].set('调试口')
        dlg.append_row()
        dlg.rows[1][0].set('COM3')
        dlg.save()
        assert dlg.result is None and 'COM3' in dlg.error.get()
        dlg.remove(dlg.rows[1])
        dlg.save()
        updated, selection = dlg.result
        assert original == {'已有': [{'port': 'COM3', 'baudrate': 9600, 'role': '控制器'}]}
        assert selection == '新项目' and updated[selection] == [{'port': 'COM3', 'baudrate': 115200, 'role': '调试口'}]
        dlg = editor.ProjectEditor(root, updated, selection, [])
        dlg.name.set('已改名')
        monkeypatch.setattr(editor.messagebox, 'askyesno', lambda *a, **kw: False)
        dlg.save()
        assert dlg.result is None
        monkeypatch.setattr(editor.messagebox, 'askyesno', lambda *a, **kw: True)
        dlg.save()
        renamed, selection = dlg.result
        assert selection == '已改名' and '新项目' not in renamed
        dlg = editor.ProjectEditor(root, renamed, selection, [])
        dlg.delete()
        assert dlg.result == (original, '全部串口')
        dlg = editor.ProjectEditor(root, renamed, selection, [])
        dlg.destroy()
        assert dlg.result is None
    finally:
        root.destroy()
