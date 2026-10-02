from __future__ import annotations

import asyncio

import pytest

from ha_tui.client import HAAuthError, HAClient, HACommandError, HAConnectionError

from .mock_ha import TOKEN, MockHA


async def test_auth_rejected(mock_ha: MockHA):
    with pytest.raises(HAAuthError):
        await HAClient(mock_ha.url, "wrong").connect()


async def test_unreachable():
    with pytest.raises(HAConnectionError):
        await HAClient("http://127.0.0.1:1", TOKEN, timeout=2).connect()


async def test_command_error(client: HAClient):
    with pytest.raises(HACommandError) as exc:
        await client.call_service("light", "explode")
    assert exc.value.code == "service_not_found"


async def test_state_subscription(client: HAClient, mock_ha: MockHA):
    seen: list[tuple[str, str | None, str | None]] = []
    unsub = await client.subscribe_states(
        lambda new, old: seen.append((new["entity_id"], old and old["state"], new["state"]))
    )
    await client.call_service("light", "toggle", target={"entity_id": "light.kitchen"})
    await asyncio.sleep(0.1)
    assert seen == [("light.kitchen", "on", "off")]
    await unsub()
    await mock_ha.set_state("light.kitchen", "on")
    await asyncio.sleep(0.1)
    assert len(seen) == 1


async def test_render_template(client: HAClient):
    assert await client.render_template("Hello {{ states('sensor.outside') }}") == "Hello 12"


async def test_service_response(client: HAClient):
    result = await client.call_service(
        "weather", "get_forecasts", target={"entity_id": "weather.home"}, return_response=True
    )
    assert result["response"] == {"weather.home": {"forecast": []}}


async def test_disconnect_callback(client: HAClient, mock_ha: MockHA):
    lost = asyncio.Event()
    client.on_disconnect(lost.set)
    await mock_ha.drop_connections()
    await asyncio.wait_for(lost.wait(), 2)
    assert not client.connected
    with pytest.raises(HAConnectionError):
        await client.get_states()
