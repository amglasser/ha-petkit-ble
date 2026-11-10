"""Home Assistant Bluetooth adapter for the Petkit BLE integration.

This module owns exactly one thing: the BLE link to one fountain.

Design rules (all of them exist because the original adapter broke them):

* Every BLE client that is abandoned for any reason is *always* disconnected.
  A BLE peripheral accepts a single connection and stops advertising while it
  has one. A client object that is merely forgotten keeps the link open inside
  the host Bluetooth stack, the fountain never advertises again and Home
  Assistant reports "device not found" until the fountain is power cycled.
* Reconnect logic lives in the coordinator, in a single supervisor task. This
  adapter never spawns reconnect tasks of its own.
* A bleak ``disconnected_callback`` tells the coordinator the moment the link
  drops, so a half-open connection is noticed immediately instead of at the
  next failed write.
* The command queue is drained on disconnect. Stale commands must never be
  replayed into a new session (the handshake is order sensitive).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum
from typing import Any, Callable

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .PetkitW5BLEMQTT.constants import Constants

_LOGGER = logging.getLogger(__name__)

# Seconds to let the fountain settle after notifications are enabled. A real
# CTW3 answered the very first command with ATT "Unlikely Error" (0x0E).
SETTLE_AFTER_NOTIFY = 1.0
WRITE_ATTEMPTS = 3
WRITE_RETRY_DELAY = 0.6
# Consecutive failed writes (each already retried) before the link is dropped.
MAX_FAILED_WRITES = 3

FRAME_HEADER = b"\xfa\xfc\xfd"
FRAME_END = 0xFB
MAX_FRAME_BUFFER = 512

CONNECT_TIMEOUT = 45.0  # hard upper bound for one establish_connection() call
WRITE_TIMEOUT = 10.0
DISCONNECT_TIMEOUT = 10.0


class ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class HABluetoothAdapter:
    """Owns the BLE link to one Petkit fountain, using Home Assistant's Bluetooth stack."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        event_handler=None,
        logger: logging.Logger | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.address = address.upper()
        self.event_handler = event_handler
        self.logger = logger or _LOGGER
        self._on_disconnect = on_disconnect

        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        self._client: BleakClientWithServiceCache | None = None
        self._last_ble_device: BLEDevice | None = None
        self._lock = asyncio.Lock()
        self._rx_buffer = bytearray()

        self._status = ConnectionStatus.DISCONNECTED
        self._last_seen: str | None = None
        self.last_rx = 0.0  # time.monotonic() of the last notification
        self._connection_attempts = 0
        self._connection_error: str | None = None

        # Diagnostics
        self.last_frames: dict[int, dict[str, Any]] = {}
        self.recent_raw: deque[tuple[str, str]] = deque(maxlen=40)
        self.parse_errors = 0
        self.char_info = ""
        self.last_write_error: str | None = None
        self._failed_writes = 0
        self.connect_count = 0
        self.disconnect_count = 0

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        """True only if we hold a client AND bleak says the link is up."""
        client = self._client
        return client is not None and client.is_connected

    @property
    def connection_status(self) -> str:
        return self._status.value

    @property
    def last_seen(self) -> str | None:
        return self._last_seen

    @property
    def connection_attempts(self) -> int:
        return self._connection_attempts

    @property
    def connection_error(self) -> str | None:
        return self._connection_error

    def ble_device_present(self) -> bool:
        """True if a connectable scanner/proxy currently sees the fountain."""
        return (
            bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            is not None
        )

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------
    def _current_ble_device(self) -> BLEDevice | None:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is not None:
            self._last_ble_device = device
        return device or self._last_ble_device

    async def connect(self) -> bool:
        """Open the BLE link and start notifications. Returns True on success."""
        async with self._lock:
            if self.is_connected:
                return True

            # Whatever was there before is dead. Release it properly first,
            # otherwise the fountain stays "occupied" and never advertises.
            await self._drop_client()

            ble_device = self._current_ble_device()
            if ble_device is None or not self.ble_device_present():
                self._register_failure(
                    "Fountain is not visible to any Bluetooth adapter or proxy"
                )
                return False

            self._set_status(ConnectionStatus.CONNECTING)
            self.logger.debug("Connecting to %s", self.address)
            try:
                client = await asyncio.wait_for(
                    establish_connection(
                        BleakClientWithServiceCache,
                        ble_device,
                        self.address,
                        self._handle_disconnected,
                        max_attempts=3,
                        ble_device_callback=self._current_ble_device,
                    ),
                    timeout=CONNECT_TIMEOUT,
                )
            except asyncio.CancelledError:
                self._set_status(ConnectionStatus.DISCONNECTED)
                raise
            except Exception as err:  # noqa: BLE001 - bleak raises many types
                self._register_failure(f"{type(err).__name__}: {err}")
                return False

            self._client = client
            self._rx_buffer.clear()

            try:
                await asyncio.wait_for(
                    client.start_notify(Constants.READ_UUID, self._handle_notification),
                    timeout=WRITE_TIMEOUT,
                )
            except asyncio.CancelledError:
                await self._drop_client()
                raise
            except Exception as err:  # noqa: BLE001
                await self._drop_client()
                self._register_failure(f"Could not start notifications: {err}")
                return False

            self.char_info = self._describe_characteristics(client)
            self._failed_writes = 0
            self.last_write_error = None
            self.logger.warning("Connected to %s; GATT: %s", self.address, self.char_info)
            await asyncio.sleep(SETTLE_AFTER_NOTIFY)
            if self._client is not client or not client.is_connected:
                self._register_failure("Link dropped right after connecting")
                return False

            self._connection_attempts = 0
            self._connection_error = None
            self.connect_count += 1
            self._set_status(ConnectionStatus.CONNECTED)
            self._update_last_seen()
            self.logger.info("Connected to %s", self.address)
            return True

    async def disconnect(self) -> None:
        """Close the BLE link and release the fountain."""
        async with self._lock:
            await self._drop_client()
            self._drain_queue()
            self._set_status(ConnectionStatus.DISCONNECTED)

    async def _drop_client(self) -> None:
        """Disconnect and forget the current client. Never raises.

        Must be called with self._lock held (or from a context where no other
        connect/disconnect can run).
        """
        client, self._client = self._client, None
        self._rx_buffer.clear()
        if client is None:
            return
        try:
            if client.is_connected:
                try:
                    await asyncio.wait_for(
                        client.stop_notify(Constants.READ_UUID), timeout=3.0
                    )
                except Exception:  # noqa: BLE001 - best effort
                    pass
            # Called even when bleak already says "not connected": the host
            # stack may still hold the link, and disconnect() on an already
            # closed client is a no-op.
            await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self.logger.debug("Error while releasing BLE client: %s", err)

    async def _invalidate(self, client: BleakClientWithServiceCache) -> None:
        """Drop `client` if it is still the active one (used after a failed write)."""
        async with self._lock:
            if self._client is not client:
                return  # already replaced by a newer connection, leave that alone
            await self._drop_client()
            self._drain_queue()
            self._set_status(ConnectionStatus.DISCONNECTED)
        self._notify_disconnect()

    def _handle_disconnected(self, client: BleakClientWithServiceCache) -> None:
        """bleak disconnected_callback. May be called from any thread."""
        self.hass.loop.call_soon_threadsafe(self._process_disconnect, client)

    def _process_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # a client we already dropped, or an old session
        self.logger.info("Fountain %s disconnected", self.address)
        self._client = None  # bleak has already torn this client down
        self._rx_buffer.clear()
        self._drain_queue()
        self.disconnect_count += 1
        self._set_status(ConnectionStatus.DISCONNECTED)
        self._notify_disconnect()

    def _notify_disconnect(self) -> None:
        if self._on_disconnect is not None:
            try:
                self._on_disconnect()
            except Exception:  # noqa: BLE001
                self.logger.exception("on_disconnect callback failed")

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    @staticmethod
    def _describe_characteristics(client: Any) -> str:
        """Properties of the two Petkit characteristics, for diagnosing write problems."""
        parts = []
        for name, uuid in (("write", Constants.WRITE_UUID), ("read", Constants.READ_UUID)):
            try:
                char = client.services.get_characteristic(uuid)
                parts.append(f"{name}={sorted(char.properties) if char else 'missing'}")
            except Exception as err:  # noqa: BLE001
                parts.append(f"{name}=unknown({type(err).__name__})")
        try:
            parts.append(f"mtu={client.mtu_size}")
        except Exception:  # noqa: BLE001
            pass
        return ", ".join(parts)

    async def write(self, data: bytes) -> bool:
        """Write one command frame.

        A GATT error on a link that is still up is retried (the fountain
        sometimes answers "Unlikely Error" when it is busy) and the link is
        only released after several operations in a row failed or as soon as
        the link itself is gone.
        """
        client = self._client
        if client is None:
            return False
        if not client.is_connected:
            await self._invalidate(client)
            return False

        last_err: Exception | None = None
        for attempt in range(WRITE_ATTEMPTS):
            # The fountain's write characteristic offers both modes, and it
            # answers write-with-response with ATT "Unlikely Error" on a CTW3.
            # Prefer write-without-response and alternate on retries.
            response: bool | None = None
            if "write-without-response" in self.char_info:
                response = False if attempt % 2 == 0 else None
            try:
                if response is None:
                    coro = client.write_gatt_char(Constants.WRITE_UUID, data)
                else:
                    coro = client.write_gatt_char(Constants.WRITE_UUID, data, response=response)
                await asyncio.wait_for(coro, timeout=WRITE_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                last_err = err
                if self._client is not client or not client.is_connected:
                    break  # the link itself is gone, retrying is pointless
                await asyncio.sleep(WRITE_RETRY_DELAY * (attempt + 1))
                continue
            self._failed_writes = 0
            self._update_last_seen()
            return True

        self._failed_writes += 1
        self.last_write_error = f"{type(last_err).__name__}: {last_err}"
        self.logger.warning(
            "Write to %s failed (cmd %s, %s consecutive): %s [GATT: %s]",
            self.address,
            data[3] if len(data) > 3 else "?",
            self._failed_writes,
            self.last_write_error,
            self.char_info,
        )
        if self._client is not client or not client.is_connected or self._failed_writes >= MAX_FAILED_WRITES:
            await self._invalidate(client)
        return False

    async def message_producer(self, message: bytes) -> None:
        """Queue a command frame. Dropped silently when not connected."""
        if not self.is_connected:
            self.logger.debug("Dropping command, not connected")
            return
        try:
            self.queue.put_nowait(bytes(message))
        except asyncio.QueueFull:
            self.logger.warning("Command queue full, dropping command")

    async def message_consumer(self) -> None:
        """Long-lived task: write queued commands to the fountain, in order."""
        while True:
            message = await self.queue.get()
            try:
                if self.is_connected:
                    await self.write(message)
                    await asyncio.sleep(0.05)
                else:
                    self.logger.debug("Discarding queued command, not connected")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.logger.exception("Unexpected error in message consumer")
            finally:
                self.queue.task_done()

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _reassemble(self, data: bytes) -> bytes | None:
        """Return a complete frame once one is available.

        With a small ATT MTU a long response arrives split over several
        notifications. A frame starts with FA FC FD and ends with FB.
        """
        if data[:3] == FRAME_HEADER:
            self._rx_buffer = bytearray(data)
        elif self._rx_buffer:
            self._rx_buffer.extend(data)
        else:
            return None  # stray fragment without a start
        if len(self._rx_buffer) > MAX_FRAME_BUFFER:
            self._rx_buffer.clear()
            return None
        if len(self._rx_buffer) >= 9 and self._rx_buffer[-1] == FRAME_END:
            frame = bytes(self._rx_buffer)
            self._rx_buffer.clear()
            return frame
        return None

    async def _handle_notification(self, sender: Any, data: bytearray) -> None:
        self._update_last_seen()
        self.last_rx = time.monotonic()
        raw = bytes(data)
        self.recent_raw.append((dt_util.utcnow().isoformat(), raw.hex()))
        frame = self._reassemble(raw)
        if frame is None:
            return
        self.last_frames[frame[3]] = {
            "time": dt_util.utcnow().isoformat(),
            "hex": frame.hex(),
        }
        if self.event_handler is None:
            return
        try:
            await self.event_handler.handle_notification(sender, bytearray(frame))
        except Exception as err:  # noqa: BLE001
            self.parse_errors += 1
            self.logger.warning(
                "Could not process frame %s (cmd %s): %s: %s",
                frame.hex(),
                frame[3],
                type(err).__name__,
                err,
            )

    # ------------------------------------------------------------------
    # Status bookkeeping
    # ------------------------------------------------------------------
    def _register_failure(self, error: str) -> None:
        self._connection_attempts += 1
        self._connection_error = error
        self._set_status(ConnectionStatus.RECONNECTING)
        # Log the first failure and then only every 10th, so a fountain that is
        # off for a day does not flood the log.
        if self._connection_attempts == 1 or self._connection_attempts % 10 == 0:
            self.logger.warning(
                "Connect to %s failed (attempt %d): %s",
                self.address,
                self._connection_attempts,
                error,
            )
        else:
            self.logger.debug("Connect failed (attempt %d): %s", self._connection_attempts, error)

    def _set_status(self, status: ConnectionStatus) -> None:
        if status is not self._status:
            self.logger.debug("Connection status %s -> %s", self._status.value, status.value)
        self._status = status

    def _update_last_seen(self) -> None:
        self._last_seen = dt_util.now().isoformat()
