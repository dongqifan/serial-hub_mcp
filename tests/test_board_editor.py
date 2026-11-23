"""Board profile fields stay separate from login / transfer actions."""
import base64
import uuid

import pytest


tk = pytest.importorskip('tkinter')
from serial_hub_mcp import board_editor


# Valid envelope prefix, not a real Windows credential. No real logins in tests.
def envelope(tail):
    return base64.b64encode(bytes.fromhex('01000000d08c9ddf0115d1118c7a00c04fc297eb') + tail).decode()


@pytest.fixture
def editor():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    board = dict(id=uuid.uuid4().hex, name='板 A', port='', baudrate=115200, serial_user='',
                 serial_secret=envelope(b'one'), web_url='', web_user='', web_secret=envelope(b'two'),
                 tftp_targets=[])
    dialog = board_editor.BoardEditor(root, board, ['COM2', 'COM3'])
    root.update()
    try:
        yield dialog
    finally:
        root.destroy()


def test_preserve_passwords_without_decrypting(editor, monkeypatch):
    def unexpected(*_):
        pytest.fail('Unchanged credentials should not be opened or replaced')
    monkeypatch.setattr(board_editor.board_secrets, 'reveal', unexpected)
    monkeypatch.setattr(board_editor.board_secrets, 'protect', unexpected)
    for k, value in {'name': '板 B', 'port': 'com3', 'baudrate': '9600', 'serial_user': 'operator',
                     'web_url': 'https://example.test/login', 'web_user': 'webuser'}.items():
        editor.values[k].set(value)
    editor.save()
    assert editor.result == {**editor.board, 'name': '板 B', 'port': 'COM3', 'baudrate': 9600,
                             'serial_user': 'operator', 'web_url': 'https://example.test/login',
                             'web_user': 'webuser'}


def test_replace_clear_and_reveal_are_explicit(editor, monkeypatch):
    serial = editor.passwords['serial_secret']
    web = editor.passwords['web_secret']
    plaintext = 'sample-only-value'
    monkeypatch.setattr(board_editor.board_secrets, 'reveal', lambda _: plaintext)
    monkeypatch.setattr(board_editor.board_secrets, 'protect', lambda _: envelope(b'new'))
    assert plaintext not in serial.note.get()
    serial.show()
    assert plaintext == serial.note.get()
    serial.show()
    assert plaintext not in serial.note.get()
    assert not serial.value.get()
    serial.value.set(plaintext)
    assert serial.entry.cget('show') == '*'
    web.clear()
    editor.save()
    assert editor.result['serial_secret'] == envelope(b'new')
    assert editor.result['web_secret'] == ''
    assert plaintext not in str(editor.result)


def test_backend_error_reveals_no_sensitive_text(editor, monkeypatch):
    def deny(_):
        raise ValueError('SAMPLE_FAILURE_INPUT')
    monkeypatch.setattr(board_editor.board_secrets, 'protect', deny)
    monkeypatch.setattr(board_editor.board_secrets, 'reveal', deny)
    web = editor.passwords['web_secret']
    web.show()
    assert 'SAMPLE_FAILURE_INPUT' not in web.note.get()
    web.value.set('SAMPLE_NEW_PASSWORD')
    editor.save()
    assert editor.result is None
    assert editor.error.get() and 'SAMPLE_' not in editor.error.get()
    assert 'SAMPLE_' not in str(editor.board)


def test_soc_targets_roundtrip_and_cancel(editor):
    root = editor.master
    first = {'soc': '主控', 'host': 'fw.test', 'port': 69}
    second = {'soc': '网络处理器', 'host': '2001:db8::1', 'port': 1069}
    entries, _ = editor.targets.add()
    assert entries['port'][0].get() == '69'
    for key in ('soc', 'host'):
        entries[key][0].set(first[key])
    editor.targets.add(second)
    editor.save()
    saved = editor.result
    assert saved['tftp_targets'] == [first, second]

    reopened = board_editor.BoardEditor(root, saved, [])
    entries, remove = reopened.targets.rows[0]
    entries['host'][0].set('different.test')
    remove.invoke()
    assert reopened.targets.values() == [second]
    reopened.destroy()
    assert reopened.result is None and saved['tftp_targets'] == [first, second]

    empty = board_editor.BoardEditor(root, saved, [])
    for _, remove in list(empty.targets.rows):
        remove.invoke()
    empty.save()
    assert empty.result['tftp_targets'] == []


def test_invalid_port_keeps_all_soc_rows_editable(editor):
    first = {'soc': '主控', 'host': 'first.test', 'port': 69}
    editor.targets.add(first)
    row, _ = editor.targets.add({'soc': '从控', 'host': 'second.test', 'port': 'bad'})
    editor.save()
    assert editor.result is None and '第 2 条' in editor.error.get()
    row['port'][0].set('1069')
    editor.save()
    assert editor.result['tftp_targets'] == [first, {'soc': '从控', 'host': 'second.test', 'port': 1069}]


def test_cancel_ignores_password_changes(editor):
    editor.passwords['web_secret'].clear()
    editor.destroy()
    assert editor.result is None
    assert editor.board['web_secret'] == envelope(b'two')
