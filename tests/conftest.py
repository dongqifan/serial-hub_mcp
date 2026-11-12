import queue

import pytest_asyncio

from serial_hub_mcp.core import Hub


class FakeSerial:
    def __init__(self):
        self.input = queue.Queue()
        self.writes = []
        self.closed = False
        self.responder = None

    def read(self, size):
        try:
            item = self.input.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data):
        self.writes.append(bytes(data))
        if self.responder:
            self.responder(data)
        return len(data)

    def close(self):
        self.closed = True


@pytest_asyncio.fixture
async def rig(tmp_path):
    devices = {}

    def factory(name, config):
        device = FakeSerial()
        devices[name] = device
        return device

    hub = Hub(tmp_path / "logs", factory=factory, capacity=128)
    yield hub, devices
    await hub.shutdown()
