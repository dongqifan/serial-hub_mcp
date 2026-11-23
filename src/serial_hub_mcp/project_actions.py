"""Project presets share physical connections. They do not own them or grant leases."""
from __future__ import annotations

from dataclasses import asdict

from .core import PortConfig


READY, IDLE, MISSING, OFFLINE, CONFLICT = '已就绪', '未占用', '未检测到', '设备断开', '参数冲突'


def member_state(member: dict, inventory: dict) -> str:
    """Chinese display label (READY / IDLE / MISSING / OFFLINE / CONFLICT). No I/O."""
    name = member['port']
    item = next((p for p in inventory['open'] if p['port'] == name), None)
    if item is None:
        return IDLE if any(p['port'] == name for p in inventory['available']) else MISSING
    if not item['connected']:
        return OFFLINE
    if PortConfig(**item['config']) != PortConfig(baudrate=member['baudrate']):
        return CONFLICT
    return READY


def _report(summary, details):
    # Leading line for a compact status bar; subsequent lines for the full result dialog.
    return '\n'.join([summary, *(f'{name}：{detail}' for name, detail in details.items())])


def _refresh(rpc, details):
    try:
        return rpc('list_ports', {})
    except Exception as exc:
        # Mutations may already have succeeded. Keep their individual replies, never retry.
        raise RuntimeError(_report(f'未能刷新最终状态。请手动刷新，不要重复操作：{exc}', details)) from exc


def claim_group(rpc, members):
    """Best-effort claim, reusing only matching live connections. Never sends data."""
    members = list(members)
    before = rpc('list_ports', {})
    details = {}
    for member in members:
        name = member['port']
        if name in details:
            continue
        state = member_state(member, before)
        if state == READY:
            details[name] = '复用现有连接'
        elif state == IDLE:
            try:
                config = PortConfig(baudrate=member['baudrate'])
                config.validate()
                rpc('open_port', {'port': name, 'config': asdict(config)})
                details[name] = '占用请求成功'
            except Exception as exc:
                details[name] = f'占用请求未成功确认：{exc}'
        else:
            details[name] = f'{state}，已跳过'
    after = _refresh(rpc, details)
    current = {m['port']: member_state(m, after) for m in members}
    ready = sum(state == READY for state in current.values())
    return after, _report(f'项目就绪 {ready}/{len(current)} 个串口。', {
        name: f'{result}；当前{current[name]}' for name, result in details.items()
    })


def release_ports(rpc, names):
    """Only attempt the names in the user's confirmation, once each. Never force close."""
    details = {}
    for name in dict.fromkeys(names):
        try:
            rpc('close_port', {'port': name})
            details[name] = '释放请求成功'
        except Exception as exc:
            details[name] = f'释放请求未成功确认：{exc}'
    after = _refresh(rpc, details)
    held = {p['port'] for p in after['open']}
    left = len(held.intersection(details))
    return after, _report(f'选定的 {len(details)} 个串口中，{len(details)-left} 个已释放，{left} 个仍被占用。', {
        name: f'{result}；当前' + ('仍被占用' if name in held else '未占用') for name, result in details.items()
    })


def group_hint(project_name, members, inventory):
    """A handoff contains this project's members only, never an unrelated connection."""
    opened = {item['port']: item for item in inventory['open']}
    lines = [f'项目组：{project_name}']
    ready = []
    for m in members:
        name = m['port']
        label = f"{name}（{m['role']}）" if m.get('role') else name
        state = member_state(m, inventory)
        if state == READY:
            item = opened[name]
            baud = PortConfig(**item['config']).baudrate
            endpoint = f"Telnet {item['telnet']}" if item.get('telnet') else '无 Telnet 入口'
            lines.append(f'{label}：已就绪，{baud} / 8N1，{endpoint}。')
            ready.append(name)
        else:
            lines.append(f'{label}：{state}，请跳过。')
    if ready:
        lines.append(f"仅对 {'、'.join(ready)} 使用已注册的 serial_hub MCP，先用 port_status 核对当前连接及参数，"
                     '匹配后用 read_output 读取已有输出。')
    else:
        lines.append('当前没有就绪的项目串口，请等待用户确认。')
    lines.append('不要打开、释放串口或发送任何数据。')
    return '\n'.join(lines)
