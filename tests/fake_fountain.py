"""A fake Petkit fountain that speaks enough of the protocol for the handshake and polling."""
from __future__ import annotations

import asyncio

from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils


def frame(cmd: int, data: list[int], seq: int = 0) -> bytes:
    return bytes(Utils.build_command(seq, cmd, 2, data))


DEVICE_ID = [0x38, 0xA5, 0x76, 0x82, 0x00, 0xF7]
SERIAL = "CTW3TESTSERIAL01"[:15]

RESPONSES: dict[int, list[int]] = {
    # identify: 2 pad bytes, 6 byte device id, 15 byte serial
    213: [0, 0] + DEVICE_ID + [ord(c) for c in SERIAL],
    # sync confirmed
    86: [1],
    # firmware 2.5
    200: [2, 5],
    # battery: 3.800 V, 80 %
    66: [0x0E, 0xD8, 80],
    # state: power on, normal mode, no dnd, no warnings, runtime 7200 s, filter 12 %, running
    210: [1, 1, 0, 0, 0, 0, 0, 0, 0x1C, 0x20, 12, 1],
    # config (14 bytes)
    211: [30, 60, 1, 80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    # full status (29+ bytes)
    230: [1, 1, 0, 0, 0, 0, 0, 0, 0x1C, 0x20, 12, 1, 0, 0, 0x0E, 0x10, 30, 60, 1, 80,
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
}


class FakeClient:
    """Stands in for BleakClientWithServiceCache."""

    def __init__(self, fountain: "FakeFountain", disconnected_callback) -> None:
        self.fountain = fountain
        self.disconnected_callback = disconnected_callback
        self.is_connected = True
        self.notify_cb = None
        self.disconnect_calls = 0
        self.writes: list[bytes] = []

    async def start_notify(self, uuid, cb):
        self.notify_cb = cb

    async def stop_notify(self, uuid):
        return None

    async def disconnect(self):
        self.disconnect_calls += 1
        self.is_connected = False

    async def write_gatt_char(self, uuid, data):
        if self.fountain.fail_writes:
            raise OSError("simulated write failure")
        if not self.is_connected:
            raise OSError("not connected")
        data = bytes(data)
        self.writes.append(data)
        cmd = data[3]
        self.fountain.commands_seen.append(cmd)
        if self.fountain.silent or cmd in self.fountain.ignore_cmds:
            return
        if cmd in RESPONSES:
            payload = RESPONSES[cmd]
            if cmd == 213 and self.fountain.zero_id:
                payload = [0, 0] + [0] * 6 + [ord(c) for c in SERIAL]
            await self.fountain.deliver(self, frame(cmd, payload))

    def remote_drop(self) -> None:
        """The fountain (or the radio) ends the link."""
        self.is_connected = False
        self.disconnected_callback(self)


class FakeFountain:
    """Hands out FakeClients and records everything."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []
        self.silent = False
        self.fail_writes = False
        self.fail_connect = False
        self.ignore_cmds: set[int] = set()
        self.commands_seen: list[int] = []
        self.fragment_size: int | None = None  # simulate a tiny MTU
        self.visible = True
        self.zero_id = False  # device reports an all-zero device id

    @property
    def current(self) -> FakeClient:
        return self.clients[-1]

    async def deliver(self, client: FakeClient, data: bytes) -> None:
        if client.notify_cb is None:
            return
        if self.fragment_size:
            for i in range(0, len(data), self.fragment_size):
                await client.notify_cb(None, bytearray(data[i : i + self.fragment_size]))
        else:
            await client.notify_cb(None, bytearray(data))

    async def establish_connection(self, client_class, device, name, disconnected_callback=None, **kwargs):
        await asyncio.sleep(0)
        if self.fail_connect:
            raise OSError("simulated connect failure")
        client = FakeClient(self, disconnected_callback)
        self.clients.append(client)
        return client
