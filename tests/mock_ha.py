"""A small fake Home Assistant WebSocket API for tests."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import threading
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

TOKEN = "test-token"
NOW = datetime.now(UTC).isoformat()


def _state(eid: str, state: str, **attrs: Any) -> dict[str, Any]:
    return {"entity_id": eid, "state": state, "attributes": attrs, "last_changed": NOW, "last_updated": NOW}


def initial_states() -> list[dict[str, Any]]:
    return [
        _state(
            "light.kitchen", "on", friendly_name="Kitchen Ceiling", brightness=128, supported_color_modes=["brightness"]
        ),
        _state(
            "light.living_room",
            "off",
            friendly_name="Living Room Lamp",
            supported_color_modes=["color_temp"],
            effect_list=["None", "Candle"],
            effect=None,
        ),
        _state("switch.coffee", "off", friendly_name="Kitchen Coffee Maker"),
        _state(
            "sensor.kitchen_temp",
            "21.5",
            friendly_name="Kitchen Temperature",
            unit_of_measurement="°C",
            device_class="temperature",
        ),
        _state(
            "climate.thermostat",
            "heat",
            friendly_name="Thermostat",
            current_temperature=20.5,
            temperature=21,
            target_temp_step=0.5,
            min_temp=7,
            max_temp=35,
            hvac_modes=["off", "heat"],
            temperature_unit="°C",
        ),
        _state(
            "media_player.tv", "playing", friendly_name="TV", media_title="Song", media_artist="Band", volume_level=0.3
        ),
        _state("cover.blinds", "open", friendly_name="Living Room Blinds", current_position=60),
        _state("scene.movie", "unknown", friendly_name="Movie time"),
        _state("sensor.router_uptime", "123", friendly_name="Router Uptime"),
        _state("automation.night", "on", friendly_name="Night mode"),
        _state("person.alice", "home", friendly_name="Alice"),
        _state("sensor.outside", "12", friendly_name="Outside Temperature", unit_of_measurement="°C"),
        _state("binary_sensor.door", "off", friendly_name="Front Door", device_class="door"),
    ]


ENTITY_REGISTRY = {
    "entity_categories": {"0": "config", "1": "diagnostic"},
    "entities": [
        {"ei": "light.kitchen", "di": "dev_kitchen", "pl": "hue"},
        {"ei": "light.living_room", "di": "dev_living", "pl": "hue"},
        {"ei": "switch.coffee", "ai": "kitchen", "pl": "tplink"},
        {"ei": "sensor.kitchen_temp", "ai": "kitchen", "pl": "zha"},
        {"ei": "climate.thermostat", "ai": "living_room", "pl": "ecobee"},
        {"ei": "media_player.tv", "ai": "living_room", "pl": "cast"},
        {"ei": "cover.blinds", "ai": "living_room", "pl": "zha"},
        {"ei": "scene.movie", "ai": "living_room", "pl": "homeassistant"},
        {"ei": "sensor.router_uptime", "ai": "kitchen", "ec": 1, "pl": "router"},
        {"ei": "automation.night", "pl": "automation"},
        {"ei": "binary_sensor.door", "di": "dev_door", "pl": "zha"},
    ],
}
DEVICES = [
    {"id": "dev_kitchen", "area_id": "kitchen", "name": "Hue Kitchen", "name_by_user": None},
    {"id": "dev_living", "area_id": "living_room", "name": "Hue Living", "name_by_user": None},
    {"id": "dev_door", "area_id": None, "name": "Door Sensor", "name_by_user": None},
]
AREAS = [
    {"area_id": "living_room", "name": "Living Room", "floor_id": "main", "temperature_entity_id": None},
    {"area_id": "kitchen", "name": "Kitchen", "floor_id": "main", "temperature_entity_id": "sensor.kitchen_temp"},
    {"area_id": "garage", "name": "Garage", "floor_id": None},
]
FLOORS = [{"floor_id": "main", "name": "Main Floor", "level": 0}]
SERVICES = {
    "light": {"turn_on": {"fields": {"brightness_pct": {"description": "Brightness"}}}, "turn_off": {}, "toggle": {}},
    "switch": {"turn_on": {}, "turn_off": {}, "toggle": {}},
    "homeassistant": {"turn_on": {}, "turn_off": {}, "toggle": {}, "update_entity": {}},
    "climate": {"set_temperature": {}, "set_hvac_mode": {}, "turn_on": {}, "turn_off": {}, "toggle": {}},
    "scene": {"turn_on": {}},
    "cover": {"open_cover": {}, "close_cover": {}, "stop_cover": {}, "set_cover_position": {}, "toggle": {}},
    "media_player": {"media_play_pause": {}, "volume_set": {}},
    "weather": {"get_forecasts": {"response": {"optional": False}}},
}
CUSTOM_DASHBOARD = {
    "title": "Custom",
    "views": [
        {
            "title": "Main",
            "path": "main",
            "badges": ["sensor.outside"],
            "cards": [
                {
                    "type": "custom:layout-card",
                    "cards": [
                        {"type": "markdown", "content": "# Hello {{ states('sensor.outside') }}"},
                        {"type": "custom:button-card", "template": "header", "variables": {"name": "LIGHTS"}},
                        {"type": "custom:button-card", "entity": "light.kitchen", "name": "Ceiling"},
                        {"type": "custom:button-card", "entity": "light.living_room", "name": "[[[ return 'x' ]]]"},
                    ],
                }
            ],
        },
        {"title": "Areas", "path": "areas", "strategy": {"type": "areas-overview"}},
    ],
}


class MockHA:
    def __init__(self) -> None:
        self.states = {s["entity_id"]: s for s in initial_states()}
        self.calls: list[dict[str, Any]] = []
        self.server: Server | None = None
        self.port = 0
        self.connections: set[ServerConnection] = set()
        self._subs: dict[ServerConnection, set[int]] = {}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self.server = await serve(self._handler, "127.0.0.1", 0)
        self.port = next(iter(self.server.sockets)).getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def drop_connections(self) -> None:
        for ws in list(self.connections):
            await ws.close()

    async def _send(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await ws.send(json.dumps(msg))

    async def set_state(self, eid: str, state: str, **attrs: Any) -> None:
        old = copy.deepcopy(self.states.get(eid))
        new = copy.deepcopy(old) if old else _state(eid, state)
        new["state"] = state
        new["attributes"].update(attrs)
        new["last_changed"] = datetime.now(UTC).isoformat()
        self.states[eid] = new
        for ws, subs in list(self._subs.items()):
            for sub_id in subs:
                event = {"event_type": "state_changed", "data": {"entity_id": eid, "old_state": old, "new_state": new}}
                with contextlib.suppress(Exception):
                    await self._send(ws, {"id": sub_id, "type": "event", "event": event})

    async def _handler(self, ws: ServerConnection) -> None:
        await self._send(ws, {"type": "auth_required", "ha_version": "2026.10.0"})
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != TOKEN:
            await self._send(ws, {"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return
        await self._send(ws, {"type": "auth_ok", "ha_version": "2026.10.0"})
        self.connections.add(ws)
        self._subs[ws] = set()
        try:
            async for raw in ws:
                await self._dispatch(ws, json.loads(raw))
        except Exception:
            pass
        finally:
            self.connections.discard(ws)
            self._subs.pop(ws, None)

    async def _dispatch(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        mid, mtype = msg["id"], msg["type"]

        async def ok(result: Any = None) -> None:
            await self._send(ws, {"id": mid, "type": "result", "success": True, "result": result})

        async def fail(code: str, message: str) -> None:
            await self._send(
                ws, {"id": mid, "type": "result", "success": False, "error": {"code": code, "message": message}}
            )

        if mtype == "get_states":
            await ok(list(self.states.values()))
        elif mtype == "get_config":
            await ok({"version": "2026.10.0", "unit_system": {"temperature": "°C"}})
        elif mtype == "get_services":
            await ok(SERVICES)
        elif mtype == "config/entity_registry/list_for_display":
            await ok(ENTITY_REGISTRY)
        elif mtype == "config/device_registry/list":
            await ok(DEVICES)
        elif mtype == "config/area_registry/list":
            await ok(AREAS)
        elif mtype == "config/floor_registry/list":
            await ok(FLOORS)
        elif mtype == "lovelace/dashboards/list":
            await ok([{"url_path": "custom", "title": "Custom", "mode": "storage"}])
        elif mtype == "lovelace/config":
            if msg.get("url_path") == "custom":
                await ok(CUSTOM_DASHBOARD)
            else:
                await fail("config_not_found", "No config found.")
        elif mtype == "subscribe_events":
            self._subs[ws].add(mid)
            await ok()
        elif mtype == "unsubscribe_events":
            self._subs[ws].discard(msg.get("subscription"))
            await ok()
        elif mtype == "render_template":
            await ok()
            rendered = msg["template"].replace("{{ states('sensor.outside') }}", self.states["sensor.outside"]["state"])
            await self._send(ws, {"id": mid, "type": "event", "event": {"result": rendered, "listeners": {}}})
        elif mtype == "history/history_during_period":
            eid = msg["entity_ids"][0]
            await ok({eid: [{"s": str(v), "lu": 0} for v in (20, 21, 21.5, 22, 21.5)]})
        elif mtype == "call_service":
            await self._call_service(msg, ok, fail)
        else:
            await ok()

    async def _call_service(self, msg: dict[str, Any], ok, fail) -> None:
        domain, service = msg["domain"], msg["service"]
        if service not in SERVICES.get(domain, {}):
            await fail("service_not_found", f"Service {domain}.{service} not found.")
            return
        self.calls.append(msg)
        data = msg.get("service_data") or {}
        if msg.get("return_response"):
            await ok({"context": {}, "response": {"weather.home": {"forecast": []}}})
            return
        await ok({"context": {}})
        targets = (msg.get("target") or {}).get("entity_id") or []
        for eid in [targets] if isinstance(targets, str) else targets:
            current = self.states.get(eid)
            if current is None:
                continue
            if service == "toggle":
                await self.set_state(eid, "off" if current["state"] == "on" else "on")
            elif service == "turn_on":
                attrs = {}
                if "brightness_step_pct" in data:
                    attrs["brightness"] = max(
                        0,
                        min(
                            255, current["attributes"].get("brightness", 0) + round(data["brightness_step_pct"] * 2.55)
                        ),
                    )
                await self.set_state(eid, "on", **attrs)
            elif service == "turn_off":
                await self.set_state(eid, "off")
            elif service == "set_temperature":
                await self.set_state(eid, current["state"], temperature=data["temperature"])
            elif service == "set_hvac_mode":
                await self.set_state(eid, data["hvac_mode"])


class ThreadedMockHA:
    """Runs MockHA on its own loop, for synchronous callers like Typer's CliRunner."""

    def __init__(self) -> None:
        self.mock = MockHA()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self) -> MockHA:
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.mock.start(), self.loop).result(5)
        return self.mock

    def __exit__(self, *exc: object) -> None:
        asyncio.run_coroutine_threadsafe(self.mock.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
