"""Local board profiles. No serial, web or TFTP operations occur on load/save."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from .board_secrets import validate_secret
from .core import HubError, PortConfig, port_name
from .projects import load_projects, project_name
from .settings import state_directory

DEFAULT_AI_PROMPT = '你的权限是最大的，不用担心其他人占用。当做你独占。可以在串口中做任何操作'

_DEFAULTS = dict(name='', port='', baudrate=115200, serial_user='', serial_secret='',
                 web_url='', web_user='', web_secret='', ai_prompt=DEFAULT_AI_PROMPT, copy_login=True)


_HEX_ID = re.compile(r'[0-9a-f]{32}')
# This namespace is only for migration. New and recreated projects always use UUID4.
_GROUP_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'serial-hub-mcp:legacy-project-groups')


@dataclass
class Project:
    id: str
    name: str
    boards: list[dict]


@dataclass
class Catalog:
    projects: list[Project]
    revision: str

    def by_id(self, project_id: str) -> Project:
        if isinstance(project_id, str) and _HEX_ID.fullmatch(project_id):
            for project in self.projects:
                if project.id == project_id:
                    return project
        raise ValueError('项目标识无效或项目不存在。')


class CatalogConflict(ValueError):
    """A cooperative writer saved a newer snapshot; reload before editing again."""


def new_project(name: str) -> Project:
    return Project(uuid.uuid4().hex, project_name(name), [])


def new_board() -> dict:
    return {'id': uuid.uuid4().hex, **_DEFAULTS, 'tftp_servers': []}


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


def _local_directory(text):
    # Offline editing is intentional: do not traverse or create filesystem objects here.
    if not text or any(c in text for c in '*?') or text.replace('\\', '/').startswith('//'):
        return False
    win = PureWindowsPath(text)
    if win.drive:
        parts = win.parts[1:]
        return (re.fullmatch(r'[A-Za-z]:', win.drive) is not None and win.is_absolute() and bool(parts)
                and all(not any(c in part for c in ':<>"|') and not part.endswith(('.', ' '))
                        and not PureWindowsPath(part).is_reserved() for part in parts))
    posix = PurePosixPath(text)
    return (os.name != 'nt' and posix.is_absolute() and len(posix.parts) > 1
            and '..' not in posix.parts and '\\' not in text)


def _servers(values):
    if not isinstance(values, list):
        raise ValueError('本机 TFTP 服务必须是列表。')
    result, names, endpoints = [], set(), set()
    for index, raw in enumerate(values, 1):
        label = f'第 {index} 个本机 TFTP 服务：'
        if not isinstance(raw, dict) or set(raw) != {'name', 'ip', 'port', 'directory'}:
            raise ValueError(label + '请填写名称、绑定 IP、UDP 端口和固件目录。')
        name = _text(raw['name'], label + '名称')
        if not name or name.casefold() in names:
            raise ValueError(label + '服务名称不能为空或与本板其他服务重名。')
        try:
            ip = ipaddress.IPv4Address(_text(raw['ip'], label + '绑定 IP'))
        except ValueError:
            raise ValueError(label + '请填写本机网卡的 IPv4 地址。') from None
        if ip.is_multicast or ip.is_reserved or int(ip) >> 24 == 0:
            raise ValueError(label + '请绑定具体的单播接口地址，不要使用通配、组播或保留地址。')
        port = _udp_port(raw['port'], label)
        if (ip, port) in endpoints:
            raise ValueError(label + '同一块板不能重复绑定同一接口和端口。')
        directory = _text(raw['directory'], label + '固件目录')
        if not _local_directory(directory):
            raise ValueError(label + '请选择本机的专用固件目录，不要使用根目录、相对路径或网络路径。')
        names.add(name.casefold())
        endpoints.add((ip, port))
        result.append({'name': name, 'ip': str(ip), 'port': port, 'directory': directory})
    return result


def normalize_board(board: dict) -> dict:
    base = (set(_DEFAULTS) - {'ai_prompt', 'copy_login'}) | {'id'}
    # Addresses from retired external-server profiles are not local listeners.
    # Remove them on normalization without resolving them or exposing them in the UI.
    retired = {'tftp_targets', 'tftp_host', 'tftp_port'}
    if not isinstance(board, dict) or (set(board) - retired - {'tftp_servers', 'ai_prompt', 'copy_login'}) != base:
        raise ValueError('开发板档案字段不完整或存在未知字段。')
    out = {k: v for k, v in board.items() if k not in retired}
    if type(out.setdefault('copy_login', True)) is not bool:
        raise ValueError('复制登录信息选项必须为布尔值。')
    if not isinstance(out.setdefault('ai_prompt', DEFAULT_AI_PROMPT), str) or '\x00' in out['ai_prompt']:
        raise ValueError('AI 提示必须是文本，不能包含空字符。')
    out['tftp_servers'] = _servers(out.get('tftp_servers', []))
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


def _normalize_projects(projects: list[Project]) -> list[Project]:
    if not isinstance(projects, list):
        raise ValueError('项目目录必须是列表。')
    clean = []
    project_names, ids = set(), set()
    for p in projects:
        if not isinstance(p, Project) or not isinstance(p.id, str) or not _HEX_ID.fullmatch(p.id):
            raise ValueError('项目标识无效。')
        name = project_name(p.name)
        if name in project_names or p.id in ids or not isinstance(p.boards, list):
            raise ValueError('项目名称或标识重复，或开发板列表无效。')
        project_names.add(name)
        ids.add(p.id)
        entries, ports, names = [], set(), set()
        for raw in p.boards:
            b = normalize_board(raw)
            if b['id'] in ids or b['name'] in names or (b['port'] and b['port'] in ports):
                raise ValueError('开发板标识不能重复；同一项目内名称和已分配的 COM 口不能重复。')
            ids.add(b['id'])
            names.add(b['name'])
            ports.add(b['port'])
            entries.append(b)
        clean.append(Project(p.id, name, entries))
    return clean


def _from_name_map(data: dict, id_for_name) -> list[Project]:
    if not isinstance(data, dict):
        raise ValueError('开发板目录格式无效。')
    projects = []
    for raw_name, entries in data.items():
        name = project_name(raw_name)
        projects.append(Project(id_for_name(name), name, entries))
    return projects


def _legacy_project_id(name: str) -> str:
    return uuid.uuid5(_GROUP_NAMESPACE, name).hex


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
                                    'port': m['port'], 'baudrate': m['baudrate'], 'tftp_servers': []})
    return result


def _unique(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError('开发板目录存在重复字段。')
    return data


def _read_catalog(path: Path) -> tuple[Catalog, bytes | None]:
    """Return the snapshot and, only for old boards.json, the exact bytes to back up."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        # No boards.json yet. Neither this nor migration creates a state directory.
        data = _migrate()
        content = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        revision = hashlib.sha256(b'projects.json\0' + content).hexdigest()
        return Catalog(_normalize_projects(_from_name_map(data, _legacy_project_id)), revision), None

    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique)
        # In the name-map format *any* name was legal (including schema_version),
        # but its value had to be a list. Never try migration after an envelope error.
        if isinstance(data, dict) and 'schema_version' in data and not isinstance(data['schema_version'], list):
            if (type(data['schema_version']) is not int or data['schema_version'] != 2
                    or set(data) != {'schema_version', 'revision', 'projects'}
                    or not isinstance(data['revision'], str) or not _HEX_ID.fullmatch(data['revision'])
                    or not isinstance(data['projects'], list)):
                raise ValueError('开发板目录版本或格式无效。')
            projects = []
            for p in data['projects']:
                if not isinstance(p, dict) or set(p) != {'id', 'name', 'boards'}:
                    raise ValueError('项目档案字段不完整或存在未知字段。')
                projects.append(Project(**p))
            return Catalog(_normalize_projects(projects), data['revision']), None

        projects = _normalize_projects(_from_name_map(data, _legacy_project_id))
        revision = hashlib.sha256(b'boards.json\0' + raw).hexdigest()
        return Catalog(projects, revision), raw
    except ValueError as exc:
        raise ValueError(f'无法读取开发板档案 {path}；原文件未修改。{exc}') from None


def load_catalog() -> Catalog:
    return _read_catalog(state_directory() / 'boards.json')[0]


@contextmanager
def _writer_lock(directory: Path):
    """Serialize cooperative writers, not readers. This is not an access control."""
    directory.mkdir(parents=True, exist_ok=True)
    # Lock a stable sidecar, not the replaced file. Never unlink it: existing
    # waiters and new writers must always open the same lock object.
    with (directory / 'boards.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            # Windows permits locking a range beyond EOF, even in an empty file.
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        # Closing the descriptor releases the lock, including on exceptions/exit.
        yield


def _atomic_write(path: Path, content: bytes, *, create_only: bool = False) -> None:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.boards-', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if create_only:
            # Publish only a complete backup. A retry must not truncate or replace
            # the first backup, even if its contents differ from today's source.
            try:
                if os.name == 'nt':
                    os.rename(tmp, path)  # Windows rename never replaces an existing file.
                else:
                    os.link(tmp, path)
            except FileExistsError:
                pass
        else:
            os.replace(tmp, path)
        # On POSIX persist the directory entry as well as the file's contents;
        # Windows does not support fsync on a directory handle via the CRT.
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def save_catalog(projects: list[Project], *, expected_revision: str) -> Catalog:
    """Compare and replace a whole snapshot. Every successful save gets a fresh revision."""
    path = state_directory() / 'boards.json'
    with _writer_lock(path.parent):
        current, backup = _read_catalog(path)  # Never overwrite a corrupt source, even on migration.
        if not isinstance(expected_revision, str) or current.revision != expected_revision:
            raise CatalogConflict('项目目录已变更，请重新载入后再保存。')
        clean = _normalize_projects(projects)
        revision = uuid.uuid4().hex
        envelope = {'schema_version': 2, 'revision': revision,
                    'projects': [{'id': p.id, 'name': p.name, 'boards': p.boards} for p in clean]}
        content = (json.dumps(envelope, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if backup is not None:
            _atomic_write(path.with_suffix('.json.bak'), backup, create_only=True)
        _atomic_write(path, content)
    return Catalog(clean, revision)


def load_boards() -> dict[str, list[dict]]:
    return {p.name: p.boards for p in load_catalog().projects}


def save_boards(data: dict[str, list[dict]]) -> None:
    """Legacy whole-map save. Only the catalog API can detect stale edits since *load*."""
    current = load_catalog()
    ids = {p.name: p.id for p in current.projects}
    projects = _from_name_map(data, lambda name: ids.get(name) or uuid.uuid4().hex)
    save_catalog(projects, expected_revision=current.revision)
