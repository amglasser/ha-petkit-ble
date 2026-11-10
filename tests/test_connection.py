"""Behavioural tests for the connection life cycle.

Each test pins down one failure of the original integration.
"""
from __future__ import annotations

import asyncio

from conftest import ADDRESS, wait_until


async def test_happy_path_connects_and_reads_values(make_coordinator, fountain):
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready), c.last_session_error
        assert await wait_until(lambda: c.device.status["filter_percentage"] == 0.12)
        status = c.device.status
        assert status["battery"] == 80
        assert status["voltage"] == 3.8
        assert c.device.firmware == 2.5
        # Model is recognised from the advertisement (0xF7 = CTW3), not defaulted to W5
        assert c.device.alias == "CTW3"
        assert c.device.product_name == "Petkit CTW3"
        # The full handshake ran in the documented order
        order = [x for x in fountain.commands_seen if x in (213, 73, 86, 84)]
        assert order[:4] == [213, 73, 86, 84], order
        assert c.is_available
    finally:
        await c.async_shutdown()


async def test_shutdown_releases_the_link(make_coordinator, fountain):
    """The original crashed in cleanup before disconnecting, leaving the fountain occupied."""
    c = await make_coordinator()
    await c.async_start()
    assert await wait_until(lambda: c._session_ready)
    client = fountain.current
    await c.async_shutdown()
    assert client.disconnect_calls >= 1
    assert not client.is_connected
    # And a second shutdown must be harmless
    await c.async_shutdown()


async def test_remote_drop_reconnects_with_single_new_client(make_coordinator, fountain):
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        first = fountain.current
        first.remote_drop()
        assert await wait_until(lambda: len(fountain.clients) == 2 and c._session_ready), (
            len(fountain.clients),
            c.last_session_error,
        )
        assert fountain.current is not first
        # exactly one reconnect, no storm of parallel attempts
        await asyncio.sleep(0.3)
        assert len(fountain.clients) == 2
    finally:
        await c.async_shutdown()


async def test_failed_write_releases_the_client(make_coordinator, fountain):
    """A write error used to delete the client without disconnecting it (leaked link)."""
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        first = fountain.current
        fountain.fail_writes = True
        # next poll will fail its writes
        assert await wait_until(lambda: first.disconnect_calls >= 1), "client leaked after write failure"
        fountain.fail_writes = False
        assert await wait_until(lambda: c._session_ready and fountain.current is not first)
    finally:
        await c.async_shutdown()


async def test_silent_link_is_reset(make_coordinator, fountain):
    """Writes succeed but the fountain never answers: half-open link, must be torn down."""
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready)
        first = fountain.current
        fountain.silent = True
        assert await wait_until(lambda: first.disconnect_calls >= 1, timeout=8), "silent link not detected"
        fountain.silent = False
        assert await wait_until(lambda: c._session_ready and fountain.current is not first)
    finally:
        await c.async_shutdown()


async def test_failed_handshake_never_leaks_a_connection(make_coordinator, fountain):
    c = await make_coordinator()
    fountain.ignore_cmds = {213}  # connects fine, never identifies itself
    await c.async_start()
    try:
        assert await wait_until(lambda: len(fountain.clients) >= 2, timeout=10)
        # every client except possibly the one currently in use was released
        for client in fountain.clients[:-1]:
            assert client.disconnect_calls >= 1, "handshake failure leaked a BLE client"
        assert not c._session_ready
        fountain.ignore_cmds = set()
        assert await wait_until(lambda: c._session_ready, timeout=10)
    finally:
        await c.async_shutdown()


async def test_waits_for_advertisement_when_fountain_not_visible(make_coordinator, fountain):
    fountain.visible = False
    c = await make_coordinator()
    await c.async_start()
    try:
        await asyncio.sleep(0.3)
        assert fountain.clients == [], "must not try to connect to an invisible device"
        assert c.adapter.connection_attempts >= 1
        fountain.visible = True
        for cb in list(fountain.adv_callbacks):
            from types import SimpleNamespace

            cb(SimpleNamespace(rssi=-70), None)
        assert await wait_until(lambda: c._session_ready, timeout=5)
    finally:
        await c.async_shutdown()


async def test_lowercase_address_is_normalised(make_coordinator, fountain):
    c = await make_coordinator(address=ADDRESS.lower())
    assert c.address == ADDRESS
    assert c.adapter.address == ADDRESS


async def test_fragmented_notifications_are_reassembled(make_coordinator, fountain):
    """With a small MTU a response arrives in several notifications."""
    fountain.fragment_size = 7
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready), c.last_session_error
        assert await wait_until(lambda: c.device.status["battery"] == 80)
        assert c.adapter.parse_errors == 0
    finally:
        await c.async_shutdown()


async def test_commands_refused_when_not_connected(make_coordinator, fountain):
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    fountain.visible = False
    c = await make_coordinator()
    with pytest.raises(HomeAssistantError):
        await c.async_set_device_mode(1, 1)


async def test_queue_cannot_block_commands_while_disconnected(make_coordinator, fountain):
    """The original queue (maxsize 10) filled up while disconnected and blocked callers forever."""
    fountain.visible = False
    c = await make_coordinator()
    for _ in range(200):
        await asyncio.wait_for(c.adapter.message_producer(b"\xfa\xfc\xfd"), 0.5)
    assert c.adapter.queue.qsize() == 0


async def test_all_zero_device_id_still_completes_handshake(make_coordinator, fountain):
    """Seen on a real CTW3: the identify reply parsed to device_id 0, which the first
    version of the handshake mistook for 'no answer' and retried forever."""
    fountain.zero_id = True
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready, timeout=8), c.last_session_error
        assert c.device.device_id == 0
        assert c.device.serial != "Uninitialized"
    finally:
        await c.async_shutdown()


async def test_transient_gatt_write_error_does_not_drop_the_link(make_coordinator, fountain):
    """A real CTW3 answered its first command with ATT 'Unlikely Error'. The link was
    then dropped and the fountain stopped advertising until power cycled."""
    fountain.transient_write_failures = 2
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready, timeout=8), c.last_session_error
        assert len(fountain.clients) == 1, "link was dropped after a transient write error"
        assert fountain.current.disconnect_calls == 0
    finally:
        await c.async_shutdown()


async def test_writes_prefer_write_without_response(make_coordinator, fountain):
    """The CTW3 rejects write-with-response with ATT 'Unlikely Error'."""
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready, timeout=8), c.last_session_error
        assert fountain.current.response_modes
        assert fountain.current.response_modes[0] is False
    finally:
        await c.async_shutdown()


async def test_home_assistant_stop_releases_the_link(make_coordinator, fountain, hass):
    """HA does not unload integrations on restart; the fountain must be released on STOP."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    c = await make_coordinator()
    await c.async_start()
    assert await wait_until(lambda: c._session_ready, timeout=8), c.last_session_error
    client = fountain.current
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    assert await wait_until(lambda: client.disconnect_calls >= 1), "link not released on HA stop"
    assert not client.is_connected


async def test_stale_link_is_released_before_first_connect(make_coordinator, fountain):
    """A link left over from a previous process hides the fountain; it is cleared at startup."""
    c = await make_coordinator()
    await c.async_start()
    try:
        assert await wait_until(lambda: c._session_ready, timeout=8), c.last_session_error
        assert fountain.stale_release_calls >= 1
    finally:
        await c.async_shutdown()


async def test_stale_link_release_is_tried_when_fountain_not_visible(make_coordinator, fountain):
    fountain.visible = False
    c = await make_coordinator()
    await c.async_start()
    try:
        await asyncio.sleep(0.3)
        assert fountain.stale_release_calls >= 2  # once at startup, once because it is invisible
    finally:
        await c.async_shutdown()
