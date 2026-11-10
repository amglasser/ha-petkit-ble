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
- "Filter Remaining" sensor fixed: it computed `100 - fraction` and so always showed ~99-100 %. It now shows the fraction of filter life remaining (assumption based on how the days-left formula uses it; verify against the Petkit app).
- Removed the unused bundled `BLEManager`/`Logger`.
- Test suite with a fake fountain (`pytest`).

Entity ids and unique ids are unchanged.

## 0.4.1
- Handshake no longer treats an all-zero device id as "no answer" to the identify command (seen on a real CTW3); it now accepts a serial number as proof the device replied.
- A failed handshake now logs the last raw notifications at WARNING level so it can be diagnosed from the HA log without a diagnostics download.

## 0.4.2
- A real CTW3 rejected its first command with ATT "Unlikely Error"; the link was then dropped and the fountain stopped advertising. GATT write errors on a live link are now retried (with a last-resort write-without-response attempt) and the link is only dropped after several failed writes in a row or when it is really gone.
- 1 s settle delay after enabling notifications, before the first command.
- GATT characteristic properties and MTU are logged on connect and shown in diagnostics.

## 0.4.3
- Parsers tolerate the shorter CTW3 payloads (cmd 211 is 12 bytes, not 14) instead of raising IndexError.
- Temporary "Frame survey" WARNING log: first frame of each command per HA run, to decode the CTW3 layout.

## 0.4.4
- Proper CTW3 decoding for cmd 210/211/230 (verified on real frames; byte map cross-checked with the open source aavdberg/ha-petkit integration). Fixes wrong Filter Remaining, Water Purified Today, runtime and the false "Breakdown" state.
- Writes prefer write-without-response (the CTW3 rejects write-with-response with ATT "Unlikely Error").
- CTW3 mode command uses [power, suspend, mode]; `set_device_config` is refused on the CTW3 until its 12 byte layout is implemented.
- Removed the temporary frame survey log.

## 0.4.5
- Root cause of "fountain unreachable after every Home Assistant restart": Home Assistant does not unload integrations on stop/restart, so the BLE link was never closed. The link stayed open inside BlueZ, the fountain kept its connection and stopped advertising. The coordinator now releases the fountain on EVENT_HOMEASSISTANT_STOP.
- At startup (and, rate limited, whenever the fountain is not visible) the integration asks BlueZ over D-Bus to drop any stale connection to the fountain (best effort, silent if unavailable). Logged at WARNING when a stale link was actually released.
- Connect timeout raised from 45 s to 120 s so a connect in progress is not cancelled half way (which can also leave the link open).

## 0.4.6
- The first 10 connect failures are all logged with their exact error (previously only the first).
- A failed connect now triggers the stale-link release, and the first few BlueZ stale-link checks log their outcome at WARNING, so it is visible whether the D-Bus call works inside Home Assistant.
