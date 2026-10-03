"""In-memory mirror of the Home Assistant data the frontend keeps in `hass`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from . import format
from .client import HAClient


@dataclass
class EntityEntry:
    entity_id: str
    device_id: str | None = None
    area_id: str | None = None
    platform: str | None = None
    entity_category: str | None = None
    hidden: bool = False
    name: str | None = None
    icon: str | None = None
    labels: list[str] = field(default_factory=list)
    display_precision: int | None = None


def parse_entity_registry_display(data: dict[str, Any]) -> dict[str, EntityEntry]:
    """Decode `config/entity_registry/list_for_display` (compressed keys)."""
    categories = {int(k): v for k, v in (data.get("entity_categories") or {}).items()}
    out: dict[str, EntityEntry] = {}
    for e in data.get("entities", []):
        ec = e.get("ec")
        out[e["ei"]] = EntityEntry(
            entity_id=e["ei"],
            device_id=e.get("di"),
            area_id=e.get("ai"),
            platform=e.get("pl"),
            entity_category=categories.get(ec) if ec is not None else None,
            hidden=bool(e.get("hb")),
            name=e.get("en"),
            icon=e.get("ic"),
            labels=e.get("lb") or [],
            display_precision=e.get("dp"),
        )
    return out


@dataclass
class Hass:
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    entities: dict[str, EntityEntry] = field(default_factory=dict)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    areas: dict[str, dict[str, Any]] = field(default_factory=dict)
    floors: dict[str, dict[str, Any]] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    panels: set[str] = field(default_factory=set)
    user_name: str | None = None
    is_admin: bool = False
    # Home panel context, loaded with the home dashboard.
    common_controls: list[str] = field(default_factory=list)
    repairs_count: int = 0

    @classmethod
    async def load(cls, client: HAClient) -> Hass:
        hass = cls()
        await hass.refresh(client)
        return hass

    async def refresh(self, client: HAClient) -> None:
        states, entities, devices, areas, floors, config, services, panels, user = await asyncio.gather(
            client.get_states(),
            client.entity_registry_display(),
            client.device_registry(),
            client.area_registry(),
            client.floor_registry(),
            client.get_config(),
            client.get_services(),
            client.send("get_panels"),
            client.send("auth/current_user"),
        )
        self.panels = set(panels or {})
        self.user_name = (user or {}).get("name")
        self.is_admin = bool((user or {}).get("is_admin"))
        self.states = {s["entity_id"]: s for s in states}
        self.entities = parse_entity_registry_display(entities)
        format.DISPLAY_PRECISION.clear()
        format.DISPLAY_PRECISION.update(
            {eid: e.display_precision for eid, e in self.entities.items() if e.display_precision is not None}
        )
        self.devices = {d["id"]: d for d in devices}
        self.areas = {a["area_id"]: a for a in areas}
        self.floors = {f["floor_id"]: f for f in floors}
        self.config = config
        self.services = services

    def apply_state(self, new_state: dict[str, Any]) -> None:
        if new_state.get("state") is None:
            self.states.pop(new_state["entity_id"], None)
        else:
            self.states[new_state["entity_id"]] = new_state

    # ------------------------------------------------------------------ #
    def name(self, entity_id: str) -> str:
        state = self.states.get(entity_id)
        if state and (friendly := state.get("attributes", {}).get("friendly_name")):
            return str(friendly)
        return entity_id.split(".", 1)[-1].replace("_", " ")

    def area_id(self, entity_id: str) -> str | None:
        entry = self.entities.get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id and (device := self.devices.get(entry.device_id)):
            return device.get("area_id")
        return None

    def area_name(self, entity_id: str) -> str | None:
        area_id = self.area_id(entity_id)
        return self.areas.get(area_id, {}).get("name") if area_id else None

    def device_name(self, device_id: str) -> str:
        device = self.devices.get(device_id, {})
        return device.get("name_by_user") or device.get("name") or "Unnamed device"

    def entity_only_name(self, entity_id: str) -> str:
        """The entity's own name without its device name (frontend name: {type: "entity"})."""
        entry = self.entities.get(entity_id)
        if entry and entry.name:
            return entry.name
        name = self.name(entity_id)
        if entry and entry.device_id:
            device = self.device_name(entry.device_id)
            if name.lower().startswith(device.lower() + " ") and len(name) > len(device) + 1:
                return name[len(device) + 1 :]
        return name

    def service_names(self) -> list[str]:
        return sorted(f"{d}.{s}" for d, services in self.services.items() for s in services)
