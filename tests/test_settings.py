from serial_hub_mcp.settings import load_token, token_file


def test_token_created_once_and_never_rotated(monkeypatch, tmp_path):
    monkeypatch.setenv("SERIAL_HUB_HOME", str(tmp_path))
    first = load_token(create=True)
    assert len(first) >= 32
    assert first == load_token(create=True) == load_token()
    assert token_file().parent == tmp_path
