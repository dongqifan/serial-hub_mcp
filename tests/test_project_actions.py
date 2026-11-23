from copy import deepcopy

import pytest

from serial_hub_mcp.project_actions import claim_group, group_hint, release_ports


def member(n, baud=115200):
    return dict(port=f'COM{n}', baudrate=baud, role=f'板卡{n}')


def conn(n, config=None, connected=True):
    return dict(port=f'COM{n}', config=config or {'baudrate': 115200}, connected=connected,
                telnet=f'127.0.0.1:{2300+n}')


def test_claim_mixed_connections_and_return_final_status():
    inventory = dict(available=[{'port': f'COM{n}'} for n in [1, 2, 3, 4, 6, 7, 8]],
                     open=[conn(1), conn(2, {'baudrate': 9600}), conn(3, {'dtr': True}),
                           conn(4, connected=False)])
    changes = []
    ops = []

    def rpc(op, args):
        ops.append(op)
        if op == 'list_ports':
            return deepcopy(inventory)
        assert op == 'open_port'
        changes.append(args)
        if args['port'] == 'COM6':
            raise ValueError('Access denied')
        inventory['open'].append(conn(int(args['port'][3:]), args['config']))

    members = [member(n) for n in range(1, 9)]
    final, result = claim_group(rpc, members)
    assert final == inventory
    assert ops == ['list_ports', 'open_port', 'open_port', 'open_port', 'list_ports']
    assert [a['port'] for a in changes] == ['COM6', 'COM7', 'COM8']
    assert changes[-1]['config'] == dict(baudrate=115200, parity='N', bytesize=8, stopbits=1,
                                         dtr=False, rts=False, flow_control='none')
    assert '3/8' in result.splitlines()[0]
    assert 'COM1：复用' in result and 'Access denied' in result and 'COM5：未检测到' in result
    assert 'COM2：参数冲突' in result and 'COM3：参数冲突' in result and 'COM4：设备断开' in result


def test_release_only_confirmed_names_once_and_continue_after_failure():
    inventory = {'available': [], 'open': [conn(n) for n in (1, 2, 3, 4)]}
    ops = []

    def rpc(op, args):
        ops.append((op, args))
        if op == 'list_ports':
            return inventory
        assert op == 'close_port'
        if args['port'] == 'COM2':
            raise ValueError('busy')
        inventory['open'] = [p for p in inventory['open'] if p['port'] != args['port']]

    final, result = release_ports(rpc, ['COM1', 'COM2', 'COM3', 'COM2'])
    assert [p['port'] for p in final['open']] == ['COM2', 'COM4']
    assert ops == [('close_port', {'port': f'COM{n}'}) for n in [1, 2, 3]] + [('list_ports', {})]
    assert '2 个已释放，1 个仍被占用' in result and 'busy' in result


def test_handoff_includes_only_matching_endpoints_and_no_unrelated_devices():
    inventory = {'available': [{'port': 'COM1'}, {'port': 'COM2'}],
                 'open': [conn(1, {'baudrate': 57600}), conn(2, {'parity': 'E'}),
                          conn(3, connected=False), conn(77)]}
    hint = group_hint('产线', [member(1, 57600), member(2), member(3), member(4)], inventory)
    assert '产线' in hint and 'COM1（板卡1）' in hint and '57600' in hint and '127.0.0.1:2301' in hint
    assert '参数冲突' in hint and '设备断开' in hint and '未检测到' in hint
    assert '2302' not in hint and '2303' not in hint and 'COM77' not in hint
    assert 'read_output' in hint and 'port_status' in hint and '不要打开' in hint
    none = group_hint('离线', [member(3)], inventory)
    assert '没有就绪' in none and 'read_output' not in none and '127.0.0.1' not in none


def test_unknown_final_state_never_retries_mutation():
    calls = []

    def rpc(op, args):
        calls.append(op)
        if op == 'list_ports':
            raise OSError('offline')

    with pytest.raises(RuntimeError, match='未能刷新最终状态') as exc:
        release_ports(rpc, ['COM2'])
    assert 'COM2' in str(exc.value) and calls == ['close_port', 'list_ports']
