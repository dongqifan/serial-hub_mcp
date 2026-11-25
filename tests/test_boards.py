import codecs
import json
import multiprocessing
import sys
import uuid

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
              serial_user='operator', web_user='web admin')
    data = {'开发组': [b, board(name='未接串口')], '空项目': []}
    boards.save_boards(data)
    assert boards.load_boards() == data
    assert b['serial_secret'] == b['web_secret'] == ''
    assert {p.name for p in tmp_path.iterdir()} == {'boards.json', 'boards.lock'}


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


@pytest.mark.parametrize('retired', [{'tftp_host': 'fw.lan', 'tftp_port': 1069},
                                     {'tftp_targets': [{'soc': '主控', 'host': '192.168.7.1', 'port': 69}]}])
def test_retired_endpoints_discarded_only_on_save(tmp_path, retired):
    import base64
    cipher = base64.b64encode(bytes.fromhex('01000000d08c9ddf0115d1118c7a00c04fc297eb') + b'fixture').decode()
    b = board(serial_secret=cipher, web_secret=cipher)
    old = {**b, **retired}
    del old['tftp_servers']
    path = tmp_path / 'boards.json'
    original = codecs.BOM_UTF8 + (json.dumps({' A ': [old], '空': []}, indent=2, ensure_ascii=False)
                                  + '\r\n').encode('utf-8')
    path.write_bytes(original)
    snapshot = boards.load_catalog()
    assert snapshot == boards.load_catalog()
    assert all(uuid.UUID(p.id).version == 5 for p in snapshot.projects)
    data = boards.load_boards()
    assert data == {'A': [b], '空': []}
    assert list(tmp_path.iterdir()) == [path] and path.read_bytes() == original

    boards.save_boards(data)
    saved = boards.load_catalog()
    assert saved.projects == snapshot.projects and saved.revision != snapshot.revision
    assert json.loads(path.read_bytes()) == {
        'schema_version': 2, 'revision': saved.revision,
        'projects': [{'id': p.id, 'name': p.name, 'boards': p.boards} for p in snapshot.projects],
    }
    assert (tmp_path / 'boards.json.bak').read_bytes() == original
    assert boards.load_boards() == data


@pytest.mark.parametrize('fields', [{'tftp_other': 'not-a-retired-field'}, {'tftp_servers': 'fw.lan:69'}])
def test_invalid_schema_is_never_overwritten(tmp_path, fields):
    b = board()
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
                          'B': [{'port': 'COM1', 'role': '', 'baudrate': 115200}], '空': []})
    old.write_text(content, encoding='utf8')
    snapshot = boards.load_catalog()
    assert snapshot == boards.load_catalog()
    assert len(snapshot.projects) == len({p.id for p in snapshot.projects}) == 3
    assert all(uuid.UUID(p.id).version == 5 for p in snapshot.projects)
    data = boards.load_boards()
    assert data['空'] == [] and list(tmp_path.iterdir()) == [old]
    assert len({b['name'] for b in data['A']}) == 4
    assert len({b['id'] for values in data.values() for b in values}) == 5
    assert data['A'][0]['id'] == uuid.uuid5(uuid.NAMESPACE_URL, 'serial-hub-mcp:A\0COM1').hex
    assert data['A'][3]['name'] == 'COM4'
    assert old.read_text(encoding='utf8') == content
    saved = boards.save_catalog(snapshot.projects, expected_revision=snapshot.revision)
    assert saved.projects == snapshot.projects
    assert not (tmp_path / 'boards.json.bak').exists()
    assert old.read_text(encoding='utf8') == content
    old.write_text('bad', encoding='utf8')
    assert boards.load_catalog() == saved and boards.load_boards() == data


def test_corruption_is_not_overwritten(tmp_path):
    path = tmp_path / 'boards.json'
    path.write_text('{"x":[],"x":[]}', encoding='utf8')
    with pytest.raises(ValueError):
        boards.save_boards({})
    assert path.read_text(encoding='utf8') == '{"x":[],"x":[]}'


def test_load_empty_catalog_creates_nothing(tmp_path, monkeypatch):
    state = tmp_path / 'not-created'
    monkeypatch.setenv('SERIAL_HUB_HOME', str(state))
    empty = boards.load_catalog()
    assert empty.projects == [] and empty.revision
    assert boards.load_catalog() == empty and boards.load_boards() == {}
    assert not state.exists()


def test_rename_delete_recreate_and_stale_snapshot(tmp_path):
    project = boards.new_project(' 甲 ')
    assert project.name == '甲' and project.boards == []
    assert project.id == uuid.UUID(project.id).hex and uuid.UUID(project.id).version == 4
    project.boards.append(board())
    first = boards.save_catalog([project], expected_revision=boards.load_catalog().revision)
    stale = boards.load_catalog()
    first.by_id(project.id).name = '新名'
    renamed = boards.save_catalog(first.projects, expected_revision=first.revision)
    assert renamed == boards.load_catalog()
    assert renamed.by_id(project.id) == boards.Project(project.id, '新名', project.boards)
    assert renamed.revision != stale.revision

    path = tmp_path / 'boards.json'
    original = path.read_bytes()
    with pytest.raises(boards.CatalogConflict):
        boards.save_catalog(stale.projects, expected_revision=stale.revision)
    assert path.read_bytes() == original

    empty = boards.save_catalog([], expected_revision=renamed.revision)
    replacement = boards.new_project('新名')
    assert replacement.id != project.id
    recreated = boards.save_catalog([replacement], expected_revision=empty.revision)
    assert recreated.by_id(replacement.id) == replacement
    errors = set()
    for bad in (project.id, None, [], '新名', 'AB' * 16, str(uuid.UUID(replacement.id))):
        with pytest.raises(ValueError) as exc:
            recreated.by_id(bad)
        errors.add(str(exc.value))
    assert len(errors) == 1  # Missing and malformed IDs reveal no input or catalog details.


def test_name_map_saves_keep_existing_ids_but_do_not_resurrect_them():
    boards.save_boards({'A': [], 'B': []})
    first = boards.load_catalog()
    a, b = first.projects
    boards.save_boards({' B ': [board()], 'A': []})
    assert [(p.id, p.name) for p in boards.load_catalog().projects] == [(b.id, 'B'), (a.id, 'A')]
    boards.save_boards({'A': []})
    boards.save_boards({'A': [], 'B': []})
    a2, b2 = boards.load_catalog().projects
    assert a2 == a and b2.name == b.name and b2.id != b.id and uuid.UUID(b2.id).version == 4
    # Same names/boards as the first save, but its revision is no longer current.
    with pytest.raises(boards.CatalogConflict):
        boards.save_catalog(first.projects, expected_revision=first.revision)


@pytest.mark.parametrize('filename', ['boards.json', 'projects.json'])
def test_stale_migration_does_not_create_backup_or_replace_source(tmp_path, filename):
    path = tmp_path / filename
    path.write_bytes(b'{"schema_version": [], "projects": [], "revision": []}')  # legal old group names
    old = boards.load_catalog()
    assert [p.name for p in old.projects] == ['schema_version', 'projects', 'revision']
    path.write_bytes(b'{"new group":[]}')
    with pytest.raises(boards.CatalogConflict):
        boards.save_catalog(old.projects, expected_revision=old.revision)
    assert path.read_bytes() == b'{"new group":[]}'
    assert not (tmp_path / 'boards.json.bak').exists()
    if filename == 'projects.json':
        assert not (tmp_path / 'boards.json').exists()


def test_backup_survives_retry_and_failed_replace(tmp_path, monkeypatch):
    path = tmp_path / 'boards.json'
    path.write_bytes(b' { "A": [] }\r\n')
    snapshot = boards.load_catalog()
    def fail_replace(*_):
        raise PermissionError('locked')
    with monkeypatch.context() as patch:
        patch.setattr(boards.os, 'replace', fail_replace)
        with pytest.raises(PermissionError):
            boards.save_catalog(snapshot.projects, expected_revision=snapshot.revision)
    backup = tmp_path / 'boards.json.bak'
    assert path.read_bytes() == backup.read_bytes() == b' { "A": [] }\r\n'
    assert boards.load_catalog() == snapshot and not list(tmp_path.glob('*.tmp'))

    # Another old file may later occupy boards.json. Never clobber the first backup.
    path.write_bytes(b'{"B": []}')
    current = boards.load_catalog()
    saved = boards.save_catalog(current.projects, expected_revision=current.revision)
    assert boards.load_catalog() == saved and backup.read_bytes() == b' { "A": [] }\r\n'
    assert not list(tmp_path.glob('*.tmp'))


def _envelope(projects, **changes):
    return {'schema_version': 2, 'revision': 'f' * 32, 'projects': projects, **changes}


def _project(**changes):
    return {'id': 'a' * 32, 'name': 'A', 'boards': [], **changes}


@pytest.mark.parametrize('bad', [
    [], _envelope([], schema_version=3), _envelope([], schema_version=2.0),
    _envelope([], extra=True), {'schema_version': 2, 'projects': []}, _envelope([], revision=None),
    _envelope({}), _envelope([{'id': 'a' * 32, 'name': 'missing boards'}]),
    _envelope([_project(extra=123)]), _envelope([_project(id='not-an-id')]),
    _envelope([_project(id='A' * 32)]), _envelope([_project(), _project(name='B')]),
    _envelope([_project(), _project(id='b' * 32, name=' A ')]),
    _envelope([_project(boards=[board(id='a' * 32)])]),
    _envelope([_project(boards=[board(id='c' * 32)]), _project(id='b' * 32, name='B', boards=[board(id='c' * 32)])]),
    _envelope([_project(boards=[board(web_secret='DO-NOT-REPEAT')])]),
])
def test_invalid_catalog_never_falls_back_or_saves(tmp_path, bad):
    path = tmp_path / 'boards.json'
    original = json.dumps(bad).encode()
    path.write_bytes(original)
    (tmp_path / 'projects.json').write_bytes(b'{"fallback": []}')
    for attempt in (boards.load_catalog, boards.load_boards, lambda: boards.save_boards({}),
                    lambda: boards.save_catalog([], expected_revision='stale')):
        with pytest.raises(ValueError) as exc:
            attempt()
        assert not isinstance(exc.value, boards.CatalogConflict) and 'DO-NOT-REPEAT' not in str(exc.value)
    assert path.read_bytes() == original and not (tmp_path / 'boards.json.bak').exists()


def test_invalid_proposed_projects_do_not_trigger_migration(tmp_path):
    path = tmp_path / 'boards.json'
    path.write_bytes(b'{"original": []}')
    revision = boards.load_catalog().revision
    p = boards.new_project('A')
    for invalid in ([p, p], [boards.Project('bad id', 'A', [])],
                    [boards.Project(p.id, p.name, [board(serial_secret='DO-NOT-REPEAT')])]):
        with pytest.raises(ValueError) as exc:
            boards.save_catalog(invalid, expected_revision=revision)
        assert 'DO-NOT-REPEAT' not in str(exc.value)
    assert path.read_bytes() == b'{"original": []}' and not (tmp_path / 'boards.json.bak').exists()


def _competing_save(home, revision, channel):
    # spawn works on both Windows and POSIX; point the independent process at the fixture explicitly.
    import os
    os.environ['SERIAL_HUB_HOME'] = str(home)
    project = boards.new_project('competitor')
    channel.send('ready')
    try:
        boards.save_catalog([project], expected_revision=revision)
    except boards.CatalogConflict:
        channel.send('conflict')
    else:
        channel.send('saved')


def test_competing_process_cannot_compare_until_replace(tmp_path, monkeypatch):
    path = tmp_path / 'boards.json'
    path.write_bytes(b'{"migrating": []}')
    original = boards.load_catalog()
    ctx = multiprocessing.get_context('spawn')
    receive, send = ctx.Pipe(duplex=False)
    contender = ctx.Process(target=_competing_save, args=(tmp_path, original.revision, send))
    replace = boards.os.replace

    def pause_before_replace(tmp, destination):
        assert destination == path
        contender.start()  # First writer has compared revisions but not published v2.
        assert receive.poll(20) and receive.recv() == 'ready'
        assert not receive.poll(0.25)  # Cannot finish while the first writer holds the lock.
        replace(tmp, destination)

    try:
        monkeypatch.setattr(boards.os, 'replace', pause_before_replace)
        winner = boards.save_catalog([boards.new_project('winner')], expected_revision=original.revision)
        assert receive.poll(20) and receive.recv() == 'conflict'
    finally:
        if contender.pid is not None:
            contender.join(10)
            if contender.is_alive():
                contender.terminate()
                contender.join(10)
        send.close()
        receive.close()
    assert contender.exitcode == 0 and boards.load_catalog() == winner
    assert (tmp_path / 'boards.json.bak').read_bytes() == b'{"migrating": []}'


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


def test_previous_tftp_catalog_and_new_servers_round_trip(tmp_path):
    path = tmp_path / 'boards.json'
    old = board(tftp_targets=[dict(soc='网桥', host='fw.lan', port=69)])
    del old['tftp_servers']
    original = json.dumps({'A': [old]}).encode()
    path.write_bytes(original)
    catalog = boards.load_boards()
    b, = catalog['A']
    assert b == {**{k: v for k, v in old.items() if k != 'tftp_targets'}, 'tftp_servers': []}
    assert path.read_bytes() == original
    b['tftp_servers'] = [dict(name='主控', ip='192.168.1.2', port=69, directory=str(tmp_path / 'firmware')),
                         dict(name='网桥', ip='127.0.0.1', port=1069, directory=r'D:\images\bridge')]
    path.write_text(json.dumps({'A': [{**old, 'tftp_servers': b['tftp_servers']}]}), encoding='utf8')
    assert boards.load_boards() == catalog  # Already-configured local services survive removal.
    boards.save_boards(catalog)
    assert boards.load_boards() == catalog
    assert not (tmp_path / 'firmware').exists()


@pytest.mark.parametrize('changes', [dict(name=''), dict(name=' a '), dict(port=69), dict(port=0), dict(port=True),
    *[dict(ip=ip) for ip in ('localhost', '0.0.0.0', '224.0.0.1', '240.0.0.1', '255.255.255.255', '::1')],
    *[dict(directory=d) for d in ('', '/', 'C:/', 'C:fw', r'\\server\share\fw', r'\\?\C:\fw', r'\\.\c:\fw',
                                  '/tmp/../', r'C:\fw\..', r'C:\fw\NUL', 'C:/fw/*', r'~\fw')]])
def test_reject_invalid_or_duplicate_servers(tmp_path, changes):
    a = dict(name='A', ip='127.0.0.1', port=69, directory=str(tmp_path / 'a'))
    b = {**a, 'name': 'B', 'port': 1069, **changes}
    with pytest.raises(ValueError, match='第 2 个本机 TFTP 服务'):
        boards.normalize_board(board(tftp_servers=[a, b]))
