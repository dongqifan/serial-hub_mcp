"""Local board profiles. No serial, web or TFTP operations occur on load/save."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .board_secrets import validate_secret
from .core import HubError, PortConfig, port_name
from .projects import load_projects, project_name
from .settings import state_directory

_DEFAULTS = dict(name='', port='', baudrate=115200, serial_user='', serial_secret='',
                 web_url='', web_user='', web_secret='')


def new_board() -> dict:
    return {'id': uuid.uuid4().hex, **_DEFAULTS, 'tftp_targets': []}


def _text(value, label):
    if not isinstance(value, str) or (value and not value.isprintable()):
        raise ValueError(f'{label}必须是单行文字。')
    return value.strip()


def _valid_host(host):
    if not host or any(c.isspace() for c in host) or '%' in host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # Numeric dotted quads must parse as IPs, not as DNS names.
    if re.fullmatch(r'[0-9.]+', host):
        return False
    try:
        host = host.removesuffix('.').encode('idna').decode('ascii')
    except UnicodeError:
        return False
    return len(host) <= 253 and all(re.fullmatch('[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                                    for label in host.split('.'))


def _valid_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in ('http', 'https') and parsed.username is None and parsed.password is None
                and not any(c in url for c in '\\?#') and not any(c.isspace() for c in url)
                and _valid_host(parsed.hostname) and not parsed.netloc.endswith(':')
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and (not parsed.netloc.startswith('[') or re.fullmatch(r'\[[^\]]+\](?::\d+)?', parsed.netloc)))
    except (ValueError, UnicodeError):
        return False


def _udp_port(value, label):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError(f'{label}端口应为 1 至 65535 之间的整数。')
    return value


def _targets(values):
    if not isinstance(values, list):
        raise ValueError('TFTP 目标必须是列表。')
    clean, names = [], set()
    for i, target in enumerate(values, 1):
        label = f'第 {i} 个 TFTP 目标：'
        if not isinstance(target, dict) or set(target) != {'soc', 'host', 'port'}:
            raise ValueError(label + '需要 SoC 名称、服务器地址和端口。')
        soc = _text(target['soc'], label + 'SoC 名称')
        if not soc or soc.casefold() in names:
            raise ValueError(label + 'SoC 名称不能为空或与本板其他目标重名。')
        names.add(soc.casefold())
        host = _text(target['host'], label + '地址')
        if not _valid_host(host):
            raise ValueError(label + '请填写服务器主机名或 IP，不要包含协议、目录或端口。')
        clean.append({'soc': soc, 'host': host, 'port': _udp_port(target['port'], label)})
    return clean


def normalize_board(board: dict) -> dict:
    base = set(_DEFAULTS) | {'id'}
    if not isinstance(board, dict) or set(board) not in (base | {'tftp_targets'}, base | {'tftp_host', 'tftp_port'}):
        raise ValueError('开发板档案字段不完整或存在未知字段。')
    out = board.copy()
    if 'tftp_targets' not in out:
        host = _text(out.pop('tftp_host'), 'TFTP 地址')
        port = _udp_port(out.pop('tftp_port'), 'TFTP ')
        out['tftp_targets'] = [{'soc': '默认 SoC', 'host': host, 'port': port}] if host else []
    out['tftp_targets'] = _targets(out['tftp_targets'])
    ident = out['id']
    if not isinstance(ident, str) or not re.fullmatch('[a-fA-F0-9]{32}', ident):
        raise ValueError('开发板标识无效。')
    out['id'] = ident.lower()
    for key, label in (('name', '开发板名称'), ('serial_user', '串口用户名'), ('web_user', 'Web 用户名'),
                       ('port', '串口'), ('web_url', 'Web 地址')):
        out[key] = _text(out[key], label)
    if not out['name']:
        raise ValueError('请输入开发板名称。')
    if out['port']:
        try:
            out['port'] = port_name(out['port'])
        except HubError:
            raise ValueError('请输入有效的 COM 串口号。') from None
    if type(out['baudrate']) is not int:
        raise ValueError('波特率必须是整数。')
    try:
        PortConfig(baudrate=out['baudrate']).validate()
    except HubError:
        raise ValueError('波特率应在 50 至 4000000 之间。') from None
    if out['web_url'] and not _valid_url(out['web_url']):
        raise ValueError('请输入 HTTP 或 HTTPS 登录地址，不要包含账号密码、查询字符串或片段。')
    for key in ('serial_secret', 'web_secret'):
        validate_secret(out[key])
    return out


def _catalog(data):
    if not isinstance(data, dict):
        raise ValueError('开发板目录格式无效。')
    clean, ids = {}, set()
    for key, entries in data.items():
        name = project_name(key)
        if name in clean or not isinstance(entries, list):
            raise ValueError('项目名称重复或开发板列表无效。')
        ports, names = set(), set()
        clean[name] = []
        for raw in entries:
            b = normalize_board(raw)
            if b['id'] in ids or b['name'] in names or (b['port'] and b['port'] in ports):
                raise ValueError('开发板标识不能重复；同一项目内名称和已分配的 COM 口不能重复。')
            ids.add(b['id'])
            names.add(b['name'])
            ports.add(b['port'])
            clean[name].append(b)
    return clean


def _migrate():
    result = {}
    for project, members in load_projects().items():
        used = set()
        result[project] = []
        for m in members:
            base = m['role'] or m['port']
            name, suffix = base, 1
            while name in used:
                suffix += 1
                name = f'{base} ({suffix})'
            used.add(name)
            ident = uuid.uuid5(uuid.NAMESPACE_URL, f'serial-hub-mcp:{project}\0{m["port"]}').hex
            result[project].append({**_DEFAULTS, 'id': ident, 'name': name,
                                    'port': m['port'], 'baudrate': m['baudrate'], 'tftp_targets': []})
    return result


def _unique(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError('开发板目录存在重复字段。')
    return data


def load_boards() -> dict[str, list[dict]]:
    path = state_directory() / 'boards.json'
    try:
        return _catalog(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_unique))
    except FileNotFoundError:
        return _migrate()
    except ValueError as exc:
        raise ValueError(f'无法读取开发板档案 {path}；原文件未修改。{exc}') from None


def save_boards(data: dict[str, list[dict]]) -> None:
    clean = _catalog(data)
    load_boards()  # Do not overwrite a corrupt source, including an unreadable legacy file.
    path = state_directory() / 'boards.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.boards-', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
