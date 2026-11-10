# Changes in this fork (0.4.0)

Upstream: https://github.com/pdiegmann/ha-petkit-ble. Licence and the original copyright notice (slespersen, MIT) are unchanged.

## Why

On a real CTW3 the connection worked for seconds to minutes, then Home Assistant lost the fountain and could not
reconnect until it was power cycled. Root causes found in the upstream code:

1. `coordinator._cleanup()` called `ConfigEntry.async_remove_update_listener`, which does not exist. It raised
   `AttributeError` *before* the BLE client was disconnected, so every unload and every failed initialisation left
   the link open inside the host Bluetooth stack. A BLE peripheral accepts one connection and stops advertising
   while it has one, so the fountain became invisible ("no scanner currently has it in its discovered devices").
2. The adapter dropped clients on write errors (`del connected_devices[...]`) without ever calling `disconnect()`.
3. No `disconnected_callback`, so a lost link was only noticed at the next failed write.
4. Reconnect tasks were spawned from several places (every refresh, every failed write, the consumer loop) and
   piled up (the log showed 187 identical "not connected" warnings).
5. The command queue (maxsize 10) filled while disconnected and blocked callers.
6. The model was recognised by name only (`W4`/`W5`/`CTW2`), so a CTW3 got W5 defaults, and an unknown service-data
   byte raised `KeyError`.
7. The options page assigned the read-only `OptionsFlow.config_entry` (breaks on current Home Assistant).

## What changed

- One supervisor task owns connect, handshake, poll and reconnect. Every abandoned client is always disconnected.
- `disconnected_callback` wakes the supervisor immediately; when the fountain is not visible it waits for its
  advertisement instead of polling.
- Silent-link watchdog: three polls without any answer reset the connection.
- Full handshake (identify, init, sync, set time) with timeouts, then one read of everything.
- Notification reassembly for small ATT MTUs.
- CTW3 (service-data id 0xF7) recognised; unknown models fall back to a generic profile.
- Entities become unavailable after 10 minutes without data instead of showing stale values; the Connection,
  Connection Attempts and Last Seen sensors are always available.
- Services raise a clear error when the fountain is not connected instead of silently doing nothing.
- Address is stored and used in upper case.
- Manifest matches on the `Petkit_*` name instead of every connectable device.
- Diagnostics download (raw frames) to help decode models whose protocol is not fully known.
- Removed the unused bundled `BLEManager`/`Logger`.
- Test suite with a fake fountain (`pytest`).

Entity ids and unique ids are unchanged.
