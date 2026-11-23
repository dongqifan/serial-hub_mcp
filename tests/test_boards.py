import json
import sys

import pytest

from serial_hub_mcp import boards
from serial_hub_mcp.board_secrets import protect, reveal, validate_secret


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))


def board(**changes):
    return {**boards.new_board(), 'name': '样板', **changes}


def test_offline_board_and_empty_project_round_trip(tmp_path):
    b = board(web_url='https://[::1]:8080/login',
              tftp_targets=[{'soc': '主控', 'host': 'fw.lan', 'port': 1069},
                            {'soc': '无线', 'host': 'fw.lan', 'port': 1069},
                            {'soc': '视频', 'host': '2001:db8::1', 'port': 69}],
              serial_user='operator', web_user='web admin')
    data = {'开发组': [b, board(name='未接串口')], '空项目': []}
    boards.save_boards(data)
    assert boards.load_boards() == data
    assert b['serial_secret'] == b['web_secret'] == ''
    assert list(tmp_path.iterdir()) == [tmp_path / 'boards.json']


def test_validate_uniqueness_and_normalization():
    first, second = board(port=' com4 '), board(port='COM4')
    boards.save_boards({'A': [first], 'B': [second]})
    saved = boards.load_boards()
    assert saved['A'][0]['port'] == 'COM4'
    for invalid in ({'A': [first], 'B': [first]}, {'A': [first, {**second, 'name': 'b'}]},
                    {'A': [first, {**second, 'port': 'COM2'}]}):
        with pytest.raises(ValueError):
            boards.save_boards(invalid)
        assert boards.load_boards() == saved


@pytest.mark.parametrize('fields', [
    {'web_url': 'https://user:mypassword@router/login'}, {'web_url': 'https://router/?token=x'},
    {'web_url': 'https://router/#token'}, {'web_url': 'http://[::1]oops/login'},
    {'web_url': 'ftp://router'}, {'web_url': 'http://router:0'}, {'web_url': 'http://router:70000'},
    {'port': 'COM0'}, {'web_secret': 'cGFzc3dvcmQ='},
    {'serial_secret': 'mypassword'}, {'serial_password': 'mypassword'}])
def test_invalid_fields_never_disclose_value(fields):
    with pytest.raises(ValueError) as exc:
        boards.normalize_board(board(**fields))
    assert 'mypassword' not in str(exc.value)


@pytest.mark.parametrize('target', [None, {}, {'soc': 'X', 'host': 'ok'},
    *[dict(soc='X', host='ok', port=p) for p in (0, True, '69', 65536)],
    *[dict(soc='X', host=h, port=69) for h in ('', 'tftp://fw.lan', 'fw.lan:69', '10.1.1.999')],
    {'soc': ' ', 'host': 'fw.lan', 'port': 69}])
def test_target_validation(target):
    with pytest.raises(ValueError, match='第 1 个 TFTP'):
        boards.normalize_board(board(tftp_targets=[target]))


def test_trim_and_reject_duplicate_soc_names():
    target = {'soc': ' Cpu  ', 'host': ' fw.lan ', 'port': 69}
    b = board(tftp_targets=[target])
    assert boards.normalize_board(b)['tftp_targets'] == [{'soc': 'Cpu', 'host': 'fw.lan', 'port': 69}]
    b['tftp_targets'].append({'soc': 'cPu', 'host': 'fw2.lan', 'port': 1069})
    with pytest.raises(ValueError, match='重名'):
        boards.normalize_board(b)
    assert board()['tftp_targets'] is not board()['tftp_targets']


@pytest.mark.parametrize('host', ['', 'fw.lan'])
def test_migrate_scalar_endpoint_on_next_save_only(tmp_path, host):
    import base64
    # Synthetic DPAPI envelope. Migration validates the envelope, never decrypts/re-encrypts it.
    cipher = base64.b64encode(bytes.fromhex('01000000d08c9ddf0115d1118c7a00c04fc297eb') + b'fixture').decode()
    old = board(tftp_host=host, tftp_port=1069, serial_secret=cipher, web_secret=cipher)
    del old['tftp_targets']
    path = tmp_path / 'boards.json'
    original = json.dumps({'A': [old]}).encode()
    path.write_bytes(original)
    data = boards.load_boards()
    migrated, = data['A']
    endpoint = [{'soc': '默认 SoC', 'host': host, 'port': 1069}] if host else []
    assert migrated == {**{k: v for k, v in old.items() if not k.startswith('tftp_')}, 'tftp_targets': endpoint}
    assert path.read_bytes() == original
    boards.save_boards(data)
    assert json.loads(path.read_bytes()) == data
    assert boards.load_boards() == data


@pytest.mark.parametrize('fields', [{'tftp_host': 'fw', 'tftp_port': 69, 'tftp_targets': []},
                                    {'tftp_host': 'fw'}, {'tftp_host': '', 'tftp_port': 0},
                                    {'tftp_targets': 'fw.lan:69'}])
def test_invalid_schema_is_never_overwritten(tmp_path, fields):
    b = board()
    b.pop('tftp_targets')
    path = tmp_path / 'boards.json'
    original = json.dumps({'A': [{**b, **fields}]}).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError):
        boards.save_boards({})
    assert path.read_bytes() == original


def test_migration_is_side_effect_free_and_stable(tmp_path):
    old = tmp_path / 'projects.json'
    content = json.dumps({'A': [{'port': port, 'role': role, 'baudrate': 9600} for port, role in
                                [('COM1', 'log'), ('COM2', 'log'), ('COM3', 'log (2)'), ('COM4', '')]],
                          'B': [{'port': 'COM1', 'role': '', 'baudrate': 115200}]})
    old.write_text(content, encoding='utf8')
    data = boards.load_boards()
    assert boards.load_boards() == data
    assert list(tmp_path.iterdir()) == [old]
    assert len({b['name'] for b in data['A']}) == 4
    assert len({b['id'] for values in data.values() for b in values}) == 5
    assert data['A'][3]['name'] == 'COM4'
    boards.save_boards(data)
    assert old.read_text(encoding='utf8') == content
    old.write_text('bad', encoding='utf8')
    assert boards.load_boards() == data


def test_corruption_is_not_overwritten(tmp_path):
    path = tmp_path / 'boards.json'
    path.write_text('{"x":[],"x":[]}', encoding='utf8')
    with pytest.raises(ValueError):
        boards.save_boards({})
    assert path.read_text(encoding='utf8') == '{"x":[],"x":[]}'


def test_empty_password_portable():
    assert protect('') == reveal('') == ''
    assert validate_secret('') is None


@pytest.mark.skipif(sys.platform == 'win32', reason='Non-Windows fail-closed behavior')
def test_no_cleartext_fallback():
    with pytest.raises(ValueError) as exc:
        protect('mypassword')
    assert 'mypassword' not in str(exc.value)


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows current-user DPAPI')
def test_actual_dpapi_round_trip(tmp_path):
    password = '模拟-密码-ñ-123©'
    cipher = protect(password)
    assert cipher != password
    boards.save_boards({'A': [board(web_secret=cipher)]})
    assert password not in (tmp_path / 'boards.json').read_text(encoding='utf8')
    assert reveal(boards.load_boards()['A'][0]['web_secret']) == password
