"""Registration tests operate only on synthetic explicitly selected files."""
import json
from pathlib import Path

import pytest

from serial_hub_mcp import desktop_registration as reg


@pytest.fixture(autouse=True)
def default_api_port(monkeypatch):
    monkeypatch.delenv('SERIAL_HUB_API_PORT', raising=False)


def test_registration_adds_exact_stdio_entry_once(tmp_path, monkeypatch):
    exe = tmp_path / 'venv with spaces' / 'Scripts' / 'python.exe'
    monkeypatch.setattr(reg.sys, 'executable', str(exe.with_name('pythonw.exe')))
    assert reg.stdio_python() == exe
    path = tmp_path / reg.CONFIG_NAME
    reg.register(path, exe)
    expected = {'mcpServers': {'serial_hub': {'command': str(exe), 'args': ['-I', '-m', 'serial_hub_mcp.mcp_server']}}}
    assert json.loads(path.read_bytes()) == expected == reg.stdio_config(exe)
    original = path.read_bytes()
    assert '相同' in reg.register(path, exe)
    assert path.read_bytes() == original and list(tmp_path.glob('*.bak')) == []


def test_custom_port_is_validated_and_existing_default_entry_is_not_overwritten(tmp_path, monkeypatch):
    path, exe = tmp_path / reg.CONFIG_NAME, Path('example/python.exe')
    reg.register(path, exe)
    original = path.read_bytes()
    monkeypatch.setenv('SERIAL_HUB_API_PORT', ' 09876 ')
    monkeypatch.setenv('UNRELATED_SECRET', 'not-for-client-config')
    assert reg.stdio_config(exe)['mcpServers']['serial_hub']['env'] == {'SERIAL_HUB_API_PORT': '9876'}
    with pytest.raises(ValueError, match='不同'):
        reg.register(path, exe)
    monkeypatch.setenv('SERIAL_HUB_API_PORT', '65536')
    with pytest.raises(ValueError, match='SERIAL_HUB_API_PORT'):
        reg.register(path, exe)
    assert path.read_bytes() == original and list(tmp_path.iterdir()) == [path]


def test_matching_legacy_entry_is_not_a_conflict_and_is_not_rewritten(tmp_path):
    path, exe = tmp_path / reg.CONFIG_NAME, Path('example/python.exe')
    data = reg.stdio_config(exe)
    data['mcpServers']['serial_hub']['args'].pop(0)
    original = json.dumps(data).encode('utf-8-sig')
    path.write_bytes(original)
    assert '旧版' in reg.register(path, exe)
    assert path.read_bytes() == original and not list(tmp_path.glob('*.bak'))


@pytest.mark.parametrize(('name', 'content'), [
    (reg.CONFIG_NAME, '{"mcpServers":{"serial_hub":{"command":"other"}}}'),
    (reg.CONFIG_NAME, '{"mcpServers":{},"mcpServers":{}}'),
    (reg.CONFIG_NAME, '{"mcpServers":[]}'), (reg.CONFIG_NAME, '{'),
    ('other-client.json', '{}'),
])
def test_refuses_conflicts_and_unsupported_files(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError):
        reg.register(path, Path('example/python.exe'))
    assert path.read_text(encoding='utf-8') == content and list(tmp_path.iterdir()) == [path]


def test_aborts_if_selected_config_changed_during_merge(tmp_path, monkeypatch):
    path = tmp_path / reg.CONFIG_NAME
    path.write_bytes(b'{}')
    original_read = reg._read
    checks = []
    def change_before_replace(selected):
        if checks:
            path.write_bytes(b'{"new": 1}')
        checks.append(selected)
        return original_read(selected)
    monkeypatch.setattr(reg, '_read', change_before_replace)
    with pytest.raises(ValueError, match='变更'):
        reg.register(path, Path('example/python.exe'))
    assert path.read_bytes() == b'{"new": 1}' and list(tmp_path.iterdir()) == [path]
