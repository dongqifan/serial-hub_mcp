"""Local project presets. Saving a preset never changes live serial connections."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .core import HubError, PortConfig, port_name
from .settings import state_directory

ALL_PORTS = '全部串口'


def _label(value: object, description: str) -> str:
    if not isinstance(value, str) or (value and not value.isprintable()):
        raise ValueError(f'{description}必须是单行文字。')
    return value.strip()


def project_name(value: object) -> str:
    name = _label(value, '项目组名称')
    if not name or name == ALL_PORTS:
        raise ValueError('请输入项目组名称，不能使用“全部串口”。')
    return name


def member(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {'port', 'baudrate', 'role'}:
        raise ValueError('串口配置含有未知字段或不是对象。')
    try:
        port = port_name(value['port']) if isinstance(value.get('port'), str) else ''
    except HubError:
        port = ''
    if not port:
        raise ValueError('请输入有效的 COM 串口名称（COM1 至 COM65535）。')
    baud = value.get('baudrate')
    if type(baud) is not int:
        raise ValueError(f'{port} 的波特率必须是整数。')
    try:
        PortConfig(baudrate=baud).validate()
    except HubError as exc:
        raise ValueError(f'{port} 的波特率必须在 50 至 4000000 之间。') from exc
    return {'port': port, 'baudrate': baud, 'role': _label(value.get('role', ''), '设备角色')}


def _validated(data: object) -> dict[str, list[dict]]:
    if not isinstance(data, dict):
        raise ValueError('项目组配置必须是 JSON 对象。')
    clean = {}
    for raw_name, entries in data.items():
        name = project_name(raw_name)
        if name in clean or not isinstance(entries, list):
            raise ValueError(f'项目组“{name}”重复或成员不是列表。')
        ports = [member(item) for item in entries]
        if len({p['port'] for p in ports}) != len(ports):
            raise ValueError(f'项目组“{name}”内不能重复添加同一串口。')
        clean[name] = ports
    return clean


def _unique_object(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError('配置中存在重复的 JSON 键。')
    return data


def _read(path: Path) -> dict[str, list[dict]]:
    try:
        return _validated(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_unique_object))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ValueError(f'无法读取项目组配置 {path}；原文件未修改。{exc}') from exc


def load_projects() -> dict[str, list[dict]]:
    return _read(state_directory() / 'projects.json')


def save_projects(projects: dict[str, list[dict]]) -> None:
    data = _validated(projects)
    path = state_directory() / 'projects.json'
    _read(path)  # Refuse to replace an invalid or unreadable configuration.
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.projects-', suffix='.tmp', delete=False) as f:
            temp_path = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
