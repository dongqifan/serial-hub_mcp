"""Project-scoped RPCs for the local development board console."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json
import re
import time
import uuid

from . import __version__, boards
from .core import Hub, HubError, PortConfig
from .project_actions import CONFLICT, IDLE, MISSING, OFFLINE, READY, member_state


STATE = {READY: 'ready', IDLE: 'available', MISSING: 'missing', OFFLINE: 'disconnected', CONFLICT: 'config_conflict'}
BOARD_OPS = frozenset({'get_project', 'open_board_serial', 'close_board_serial', 'read_board_output',
                       'send_board_text', 'send_board_bytes', 'execute_board'})
GUI_OPS = frozenset({'console_status', 'console_report_tftp', 'console_forget_tftp', 'console_stop'})


def _id(value):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{32}', value) is None:
        raise HubError('Expected a project, board or window ID (32 lowercase hex characters).')
    return value


def _binding(project_id, board):
    fields = [project_id, board['id'], board['port'], board['baudrate']]
    return hashlib.sha256(json.dumps(fields, separators=(',', ':')).encode()).hexdigest()


def _public_status(snapshot):
    return {key: snapshot[key] for key in ('connected', 'input_mode', 'cursor_start', 'cursor_end', 'telnet',
                                            'active_executions', 'write_sequence', 'terminal_clients',
                                            'pending_writes', 'write_in_progress', 'journal_dropped_events')}


class WindowReports:
    ttl = 15.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.windows: dict[str, tuple[float, set[tuple]]] = {}

    def expire(self):
        now = self.clock()
        for window_id, (deadline, _) in list(self.windows.items()):
            if deadline <= now:
                del self.windows[window_id]

    def report(self, window_id, services):
        self.expire()
        window_id = _id(window_id)
        if not isinstance(services, list) or (window_id not in self.windows and len(self.windows) >= 64):
            raise HubError('Invalid or excessive TFTP window report')
        entries = set()
        for cfg in services:
            if (not isinstance(cfg, dict) or set(cfg) != {'board_id', 'name', 'ip', 'port', 'directory'}
                    or any(not isinstance(cfg[k], str) for k in ('name', 'ip', 'directory'))
                    or type(cfg['port']) is not int or not 1 <= cfg['port'] <= 65535):
                raise HubError('Invalid TFTP listener report')
            entries.add((_id(cfg['board_id']), cfg['name'], cfg['ip'], cfg['port'], cfg['directory']))
        self.windows[window_id] = self.clock() + self.ttl, entries

    def active(self):
        self.expire()
        return set().union(*(entries for _, entries in self.windows.values()))


class ProjectService:
    def __init__(self, hub: Hub, request_stop: Callable[[], None] | None = None):
        self.hub = hub
        self.request_stop = request_stop
        self.instance_id = uuid.uuid4().hex
        self.tftp = WindowReports()

    async def project(self, project_id):
        _id(project_id)
        try:
            catalog = await asyncio.to_thread(boards.load_catalog)
        except (OSError, ValueError):
            raise HubError('Cannot read the board catalog. Check the local GUI; no hardware was changed.') from None
        try:
            return catalog.by_id(project_id)
        except ValueError:
            raise HubError('Unknown project ID. Copy the project ID from the board console.') from None

    async def describe(self, project):
        inventory = {'available': [], 'open': []}
        enumeration_failed = False
        if any(b['port'] for b in project.boards):
            try:
                inventory = await self.hub.inventory()
            except OSError:
                enumeration_failed = True
                inventory['open'] = [port.snapshot() for port in self.hub.ports.values()]
        opened = {item['port']: item for item in inventory['open']}
        active = self.tftp.active()
        results = []
        for b in project.boards:
            name = b['port']
            config = PortConfig(baudrate=b['baudrate'])
            state = STATE[member_state(b, inventory)] if name else 'unbound'
            if enumeration_failed and state == 'missing':
                state = 'unknown'
            binding = _binding(project.id, b) if name else None
            serial = {**asdict(config), 'port': name or None, 'state': state,
                      'binding_id': binding, 'connection_id': None}
            snap = opened.get(name)
            if snap is not None and PortConfig(**snap['config']) == config:
                epoch = snap['cursor_end'].partition(':')[0]
                serial.update(_public_status(snap), connection_id=f'{binding}:{epoch}')
            resources = []
            for cfg in b['tftp_servers']:
                key = (b['id'], cfg['name'], cfg['ip'], cfg['port'], cfg['directory'])
                status = ('reported_running' if key in active else
                          'not_reported_running' if self.tftp.windows else 'unknown')
                resources.append({k: cfg[k] for k in ('name', 'ip', 'port', 'directory')} | {'state': status})
            results.append({'board_id': b['id'], 'name': b['name'], 'serial': serial,
                            'web_url': b['web_url'] or None, 'tftp_servers': resources,
                            'operator_note': b['ai_prompt']})
        return {'project_id': project.id, 'name': project.name, 'boards': results}

    async def board_rpc(self, op, args, owner):
        project = await self.project(args['project_id'])
        if op == 'get_project':
            return await self.describe(project)
        board_id = _id(args['board_id'])
        b = next((item for item in project.boards if item['id'] == board_id), None)
        if b is None:
            raise HubError('Board does not belong to this project.')
        if not b['port']:
            raise HubError('No serial port is configured for this board.')
        if 'port' in args or 'config' in args:
            raise HubError('Board operations use the saved port and parameters only.')
        binding = _binding(project.id, b)
        config = PortConfig(baudrate=b['baudrate'])
        if op == 'open_board_serial':
            if args['binding_id'] != binding:
                raise HubError('Board binding changed. Read get_project again.')
            snap = await self.hub.open(b['port'], config)
            if not snap['connected']:
                raise HubError('Serial port is disconnected; it was not reopened.')
            epoch = snap['cursor_end'].partition(':')[0]
            return {'project_id': project.id, 'board_id': b['id'], 'connection_id': f'{binding}:{epoch}',
                    **_public_status(snap)}
        port = self.hub.ports.get(b['port'])
        if port is None or port.config != config or args['connection_id'] != f'{binding}:{port.epoch}':
            raise HubError('Stale board connection or conflicting serial configuration. Read get_project again.')
        if op == 'close_board_serial':
            return await self.hub.close_port(b['port'], owner, expected_epoch=port.epoch)
        if op == 'read_board_output':
            return await port.read(args.get('cursor'), args.get('max_bytes', 16384),
                                   args.get('wait_seconds', 0), args.get('encoding', 'utf-8'))
        if op == 'execute_board':
            return await port.execute(owner, args['text'], args['expect'], args.get('timeout', 10),
                                      newline=args.get('newline', '\r'), encoding=args.get('encoding', 'utf-8'))
        if op == 'send_board_text':
            data = args['text'].encode(args.get('encoding', 'utf-8'))
        elif op == 'send_board_bytes':
            data = base64.b64decode(args['base64_data'], validate=True)
        else:
            raise HubError('Unknown board operation')
        sent = await port.send(owner, data)
        return {'written': sent['written'], **_public_status(sent)}

    async def management(self, op, args):
        if op == 'console_status':
            capabilities = ['projects', 'board_serial', 'tftp_status']
            if self.request_stop:
                capabilities.append('idle_stop')
            return {'name': 'Development Board Console', 'version': __version__, 'instance_id': self.instance_id,
                    'open_ports': sorted(self.hub.ports), 'capabilities': capabilities, 'stopping': self.hub.stopping}
        if op == 'console_report_tftp':
            if self.hub.stopping:
                raise HubError('Console service is stopping.')
            self.tftp.report(args['window_id'], args['services'])
            return {'reported': True}
        if op == 'console_forget_tftp':
            self.tftp.windows.pop(_id(args['window_id']), None)
            return {'forgotten': True}
        if op == 'console_stop':
            async with self.hub.lifecycle:
                if not self.request_stop or args['instance_id'] != self.instance_id:
                    raise HubError('Cannot stop this service instance. Refresh its status.')
                if self.hub.ports or self.tftp.active():
                    raise HubError('Release open serial ports and stop active TFTP listeners in the GUI first.')
                self.hub.stopping = True
                self.request_stop()
                return {'stopping': True}
        raise HubError('Unknown console operation')
