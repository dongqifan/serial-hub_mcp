"""Translate board profiles to serial operations; serial RPC never receives credentials."""
from __future__ import annotations

from . import board_secrets
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


def board_hint(project, boards, inventory, *, running_services=None):
    # Build the whole handoff before touching the clipboard.
    lines = [f'项目：{project}']
    opened = {p['port']: p for p in inventory['open']} if inventory is not None else {}
    for b in boards:
        state = board_state(b, inventory) if inventory is not None or not b['port'] else '状态未知'
        lines.append(f"\n开发板：{b['name']}")
        if state == READY:
            live = opened[b['port']]
            lines.append(f"串口：{b['port']}，{live['config']['baudrate']} / 8N1，已就绪。")
            if live.get('telnet'):
                lines.append(f"本机 Telnet：{live['telnet']}")
        else:
            lines.append(f"串口：{b['port'] or '无'}（{state}）。")
        if b['web_url']:
            lines.append(f"Web 登录地址（配置值）：{b['web_url']}")
        if b['copy_login']:
            for key, label in (('serial', '串口'), ('web', 'Web ')):
                if b[key + '_user']:
                    lines.append(f"{label}用户名：{b[key + '_user']}")
                if b[key + '_secret']:
                    try:
                        password = board_secrets.reveal(b[key + '_secret'])
                    except Exception:
                        raise ValueError(f"无法解密“{b['name']}”的{label}密码。本次未复制，请重新保存密码或取消勾选复制登录信息。") from None
                    lines.append(f'{label}密码：{password}')
        for service in b['tftp_servers']:
            key = b['id'], service['name']
            status = ('运行状态未知' if running_services is None else
                      '本窗口运行中' if key in running_services else '本窗口未启动')
            lines.append(f"本机只读 TFTP · {service['name']}：{service['ip']}:{service['port']} / UDP"
                         f"（{status}），固件目录：{service['directory']}")
        if b['ai_prompt']:
            lines.extend(('本板使用者给 AI 的提示：', b['ai_prompt']))
    return '\n'.join(lines)
