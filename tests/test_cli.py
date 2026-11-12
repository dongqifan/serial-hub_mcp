import json
import sys

import httpx
import pytest

from serial_hub_mcp import cli
from serial_hub_mcp.core import HubError


@pytest.mark.parametrize("command", ["open", "occupy"])
def test_open_command_passes_serial_parameters(monkeypatch, capsys, command):
    calls = []
    monkeypatch.setattr(sys, "argv", ["serial-hub", command, "com3", "--baudrate", "9600",
                                    "--parity", "E", "--flow-control", "rtscts", "--dtr"])
    monkeypatch.setattr(cli, "rpc", lambda op, args: calls.append((op, args)) or {"port": "COM3"})
    cli.main()
    op, args = calls[0]
    assert op == "open_port" and args["port"] == "COM3"
    assert args["config"] == dict(baudrate=9600, bytesize=8, parity="E", stopbits=1,
                                  flow_control="rtscts", dtr=True, rts=False)
    assert json.loads(capsys.readouterr().out)["port"] == "COM3"


@pytest.mark.parametrize("command,operation,args", [
    (["close", "COM3"], "close_port", {"port": "COM3"}),
    (["release", "COM3"], "close_port", {"port": "COM3"}),
    (["status", "COM3"], "port_status", {"port": "COM3"}),
    (["status"], "list_ports", {}),
    (["ports", "--json"], "list_ports", {}),
])
def test_management_commands(monkeypatch, capsys, command, operation, args):
    calls = []
    monkeypatch.setattr(sys, "argv", ["serial-hub", *command])
    monkeypatch.setattr(cli, "rpc", lambda op, arguments: calls.append((op, arguments)) or {})
    cli.main()
    assert calls == [(operation, args)]
    assert json.loads(capsys.readouterr().out) == {}


def test_ports_distinguishes_hub_ownership_from_system_availability(capsys):
    cli.show_ports({"available": [{"port": "COM8"}], "open": [
        {"port": "COM3", "connected": True, "config": {"baudrate": 115200},
         "telnet": "127.0.0.1:2303", "terminal_clients": 2, "active_executions": 1},
    ]})
    text = capsys.readouterr().out
    assert "COM3" in text and "115200" in text and "127.0.0.1:2303" in text
    assert "COM8" in text and "not-open" in text
    assert "another program may still own it" in text


@pytest.mark.parametrize("error", [HubError("Port has terminal clients or active execute calls"),
                                  ValueError("Cannot open COM3; another program may own it"),
                                  httpx.ConnectError("connection refused")])
def test_errors_are_actionable_without_tracebacks(monkeypatch, capsys, error):
    monkeypatch.setattr(sys, "argv", ["serial-hub", "release", "COM3"])
    def fail(*args):
        raise error
    monkeypatch.setattr(cli, "rpc", fail)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    assert str(error) in capsys.readouterr().err


def test_invalid_port_is_argument_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["serial-hub", "open", "COM0"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    assert "COM1..COM65535" in capsys.readouterr().err
