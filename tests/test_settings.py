import pytest

from serial_hub_mcp.settings import api_url, state_directory


def test_settings_do_not_create_state_or_token(monkeypatch, tmp_path):
    directory = tmp_path / "state"
    monkeypatch.setenv("SERIAL_HUB_HOME", str(directory))
    monkeypatch.setenv("SERIAL_HUB_API_PORT", "8766")
    assert state_directory() == directory
    assert api_url() == "http://127.0.0.1:8766"
    assert not directory.exists()


@pytest.mark.parametrize("value", ["0", "65536", "remote.example:8766"])
def test_api_port_cannot_change_loopback_host(monkeypatch, value):
    monkeypatch.setenv("SERIAL_HUB_API_PORT", value)
    with pytest.raises(ValueError):
        api_url()
