from serial_hub_mcp.board_operations import board_hint, claim_boards
from serial_hub_mcp.boards import new_board


def test_network_only_profile_is_not_opened_or_shared_with_credentials():
    b = dict(new_board(), name='网络板', web_url='https://dut.test/login',
             tftp_targets=[dict(soc='主控', host='fw.test', port=69), dict(soc='视觉', host='::1', port=1069)],
             serial_user='serial-identity', web_user='web-identity', serial_secret='secret-a', web_secret='secret-b')
    calls = []
    inventory = {'open': [], 'available': []}

    def rpc(op, args):
        calls.append((op, args))
        return inventory

    _, notice = claim_boards(rpc, [b])
    text = board_hint('实验室', [b], inventory)
    assert all(op == 'list_ports' and not args for op, args in calls)
    assert '未绑定串口' in notice and '未绑定串口' in text
    assert 'https://dut.test/login' in text and '主控：fw.test:69\n视觉：[::1]:1069' in text
    for key in ('serial_user', 'web_user', 'serial_secret', 'web_secret'):
        assert b[key] not in text
