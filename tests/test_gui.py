"""Exercise the actual Tk buttons with a simulated device (xvfb-run on Linux)."""
import time

import pytest


tk = pytest.importorskip('tkinter')
from serial_hub_mcp import gui


def settle(panel):
    end = time.monotonic() + 5
    while panel.busy and time.monotonic() < end:
        panel.root.update()
        time.sleep(0.01)
    assert not panel.busy
    panel.root.update()


def test_gui_claim_handoff_release(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'No Tk display: {exc}')
    inventory = {'available': [{'port': 'COM9', 'description': 'USB UART'}], 'open': []}
    calls = []

    def rpc(operation, args):
        calls.append(operation)
        if operation == 'open_port':
            inventory['open'] = [dict(port=args['port'], config=args['config'], connected=True,
                                      telnet='127.0.0.1:2657', terminal_clients=0, active_executions=0)]
        elif operation == 'close_port':
            if inventory['open'][0]['terminal_clients']:
                raise ValueError('Port has terminal clients or active execute calls')
            inventory['open'] = []
        return inventory

    monkeypatch.setattr(gui, 'rpc', rpc)
    monkeypatch.setattr(gui, 'ensure_service', lambda: rpc('list_ports', {}))
    monkeypatch.setattr(gui.messagebox, 'askyesno', lambda *a, **kw: True)
    panel = gui.Console(root)
    try:
        settle(panel)
        assert calls == ['list_ports']
        assert panel.copy.instate(['disabled'])
        panel.baud.set('0')
        panel.claim.invoke()
        assert calls == ['list_ports']
        panel.baud.set('9600')
        panel.claim.invoke()
        settle(panel)
        assert inventory['open'][0]['config']['baudrate'] == 9600
        assert panel.table.set('COM9', 'endpoint') == '127.0.0.1:2657'
        panel.copy.invoke()
        assert root.clipboard_get() == '127.0.0.1:2657'
        panel.handoff.invoke()
        assert 'COM9' in root.clipboard_get()
        inventory['open'][0]['terminal_clients'] = 1
        panel.release.invoke()
        settle(panel)
        assert panel.release.instate(['!disabled'])
        assert 'terminal clients' in panel.status.get()
        inventory['open'][0]['terminal_clients'] = 0
        panel.release.invoke()
        settle(panel)
        assert panel.table.set('COM9', 'endpoint') == '—'
        assert panel.claim.instate(['!disabled'])
        assert panel.handoff.instate(['disabled'])
        assert calls == ['list_ports', 'open_port', 'list_ports', 'close_port', 'list_ports',
                         'close_port', 'list_ports']
    finally:
        panel.close()
