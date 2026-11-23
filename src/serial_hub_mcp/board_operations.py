"""Translate board profiles to serial operations; credentials never enter RPC or AI text."""
from __future__ import annotations

from .project_actions import READY, claim_group, member_state, release_ports

__all__ = ['board_state', 'claim_boards', 'board_hint', 'release_ports']


def _member(board):
    return {'port': board['port'], 'baudrate': board['baudrate'], 'role': board['name']}


def board_state(board, inventory):
    return member_state(_member(board), inventory) if board['port'] else '未绑定串口'


def claim_boards(rpc, boards):
    inventory, report = claim_group(rpc, [_member(b) for b in boards if b['port']])
    skipped = [f"{b['name']}：未绑定串口，已跳过" for b in boards if not b['port']]
    return inventory, '\n'.join([report, *skipped])


def tftp_lines(board):
    for target in board['tftp_targets']:
        host = target['host']
        address = f'[{host}]' if ':' in host else host
        yield f"{target['soc']}：{address}:{target['port']}"


def board_hint(project, boards, inventory):
    # Explicit allow-list: neither ciphertext nor login identifiers belong in a handoff.
    lines = [f'项目：{project}']
    opened = {p['port']: p for p in inventory['open']}
    for b in boards:
        state = board_state(b, inventory)
        lines.append(f"\n开发板：{b['name']}")
        if state == READY:
            live = opened[b['port']]
            lines.append(f"串口：{b['port']}，{live['config']['baudrate']} / 8N1，已就绪。")
            if live.get('telnet'):
                lines.append(f"本机 Telnet：{live['telnet']}")
        else:
            lines.append(f"串口：{b['port'] or '无'}（{state}），跳过串口操作。")
        if b['web_url']:
            lines.append(f"Web 登录地址（配置值）：{b['web_url']}")
        if b['tftp_targets']:
            lines.append('固件 TFTP（配置值，UDP）：')
            lines.extend(tftp_lines(b))
    lines.append('\n仅对已就绪的串口使用已注册的 serial_hub MCP；先用 port_status 核对，再用 read_output 读取。'
                 '不要打开或释放串口、发送数据、尝试登录或升级固件。')
    return '\n'.join(lines)
