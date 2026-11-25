from serial_hub_mcp.board_operations import board_hint, claim_boards
from serial_hub_mcp.boards import new_board


def test_network_only_profile_is_not_opened_or_shared_with_credentials():
    b = dict(new_board(), name='网络板', copy_login=False, web_url='https://dut.test/login',
             tftp_servers=[dict(name='主控', ip='192.0.2.1', port=69, directory='D:/fw/main'),
                           dict(name='视觉', ip='192.0.2.1', port=1069, directory='D:/fw/vision')],
             serial_user='serial-identity', web_user='web-identity', serial_secret='secret-a', web_secret='secret-b')
    calls = []
    inventory = {'open': [], 'available': []}

    def rpc(op, args):
        calls.append((op, args))
        return inventory

    _, notice = claim_boards(rpc, [b])
    text = board_hint('实验室', [b], inventory, running_services={(b['id'], '视觉')})
    assert all(op == 'list_ports' and not args for op, args in calls)
    assert '未绑定串口' in notice and '未绑定串口' in text
    assert 'https://dut.test/login' in text and '主控：192.0.2.1:69 / UDP（本窗口未启动），固件目录：D:/fw/main' in text
    assert '视觉：192.0.2.1:1069 / UDP（本窗口运行中），固件目录：D:/fw/vision' in text
    assert '状态未知' in board_hint('实验室', [dict(b, port='COM9')], None)
    for key in ('serial_user', 'web_user', 'serial_secret', 'web_secret'):
        assert b[key] not in text


def test_prompt_is_per_board_and_empty_means_no_instruction(tmp_path, monkeypatch):
    import json
    from serial_hub_mcp.boards import DEFAULT_AI_PROMPT, load_boards, save_boards
    monkeypatch.setenv('SERIAL_HUB_HOME', str(tmp_path))
    old = dict(new_board(), name='A'); del old['ai_prompt']
    other = dict(new_board(), name='B', ai_prompt='读产品版本。\n再汇总。')
    (tmp_path / 'boards.json').write_text(json.dumps({'G': [old, other]}), encoding='utf8')
    data = load_boards()
    text = board_hint('G', data['G'], None)
    assert text.index(DEFAULT_AI_PROMPT) < text.index('开发板：B') < text.index(other['ai_prompt'])
    assert '不要打开或释放串口' not in text
    data['G'][0]['ai_prompt'] = ''
    save_boards(data)
    assert board_hint('G', load_boards()['G'][:1], None).endswith('（未绑定串口）。')


def test_group_credentials_follow_individual_flags(monkeypatch):
    from serial_hub_mcp import board_secrets
    from serial_hub_mcp.boards import normalize_board
    import pytest
    old = dict(new_board(), name='A', serial_user='serial-a', web_user='web-a', serial_secret='one', web_secret='two')
    old.pop('copy_login')
    # Historical profiles default on; only simulate ciphertext for the handoff itself.
    assert normalize_board(dict(old, serial_secret='', web_secret=''))['copy_login'] is True
    enabled = dict(old, copy_login=True)
    disabled = dict(new_board(), name='B', copy_login=False, serial_user='hidden-user', serial_secret='hidden-secret')
    calls = []
    monkeypatch.setattr(board_secrets, 'reveal', lambda c: calls.append(c) or {'one':'p1','two':'p2'}[c])
    text = board_hint('G', [enabled, disabled], None)
    assert calls == ['one','two'] and 'hidden' not in text
    assert all(s in text for s in ('串口用户名：serial-a', '串口密码：p1', 'Web 用户名：web-a', 'Web 密码：p2'))
    def fail(_):
        raise ValueError('private detail')
    monkeypatch.setattr(board_secrets, 'reveal', fail)
    with pytest.raises(ValueError, match='本次未复制') as e:
        board_hint('G', [enabled], None)
    assert 'private detail' not in str(e.value)
