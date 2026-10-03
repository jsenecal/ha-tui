"""Port of the frontend's "home" dashboard strategy (the new Overview / Home panel).

Mirrors home-assistant/frontend src/panels/lovelace/strategies/home and the
light / climate / security / maintenance panel strategies its summary cards
link to. Summary and area cards are emitted as small custom card types
(`home-summary`, `area`, `ha-tui:*`) that the converter turns into live rows.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..format import domain_of
from ..hass import Hass
from .strategies import strip_prefix

Config = dict[str, Any]
EntityFilter = Callable[[str], bool]
_ANY = object()

ASSIST_ENTITIES = ["assist_satellite", "conversation", "stt", "tts"]
LOW_BATTERY_THRESHOLD = 20


def make_filter(
    hass: Hass,
    *,
    domain: str | Iterable[str] | None = None,
    device_class: str | Iterable[str] | None = None,
    area: Any = _ANY,
    entity_category: str | Iterable[str] | None = None,
    hidden_platform: Iterable[str] | None = None,
    hidden_domains: Iterable[str] | None = None,
) -> EntityFilter:
    """frontend generateEntityFilter. `area=None` means "has no area"."""

    def as_set(v: Any) -> set[Any] | None:
        if v is None:
            return None
        return {v} if isinstance(v, str) else set(v)

    domains, dcs, cats = as_set(domain), as_set(device_class), as_set(entity_category)
    hidden_p, hidden_d = as_set(hidden_platform), as_set(hidden_domains)
    areas = None if area is _ANY else ({area} if area is None or isinstance(area, str) else set(area))

    def check(eid: str) -> bool:
        state = hass.states.get(eid)
        if state is None:
            return False
        d = domain_of(eid)
        if domains is not None and d not in domains:
            return False
        if hidden_d is not None and d in hidden_d:
            return False
        if dcs is not None and (state.get("attributes", {}).get("device_class") or "none") not in dcs:
            return False
        entry = hass.entities.get(eid)
        if entry is not None and entry.hidden:
            return False
        if areas is not None and hass.area_id(eid) not in areas:
            return False
        if cats is not None and ((entry.entity_category if entry else None) or "none") not in cats:
            return False
        return hidden_p is None or (entry is not None and not (entry.platform and entry.platform in hidden_p))

    return check


def find_entities(hass: Hass, filters: list[EntityFilter], among: Iterable[str] | None = None) -> list[str]:
    """frontend findEntities: filter order first, then entity order, de-duplicated."""
    pool = list(among if among is not None else hass.states)
    out: list[str] = []
    seen: set[str] = set()
    for f in filters:
        for eid in pool:
            if eid not in seen and f(eid):
                seen.add(eid)
                out.append(eid)
    return out


# ---------------------------------------------------------------------- #
# summaries
# ---------------------------------------------------------------------- #
SUMMARY_LABELS = {
    "light": "Lights",
    "climate": "Climate",
    "security": "Security",
    "media_players": "Media players",
    "maintenance": "Maintenance",
    "energy": "Energy",
    "persons": "People",
    "weather": "Weather",
    "repairs": "Repairs",
    "updates": "Updates",
}
SUMMARY_ICONS = {
    "light": "💡",
    "climate": "🔥",
    "security": "🔒",
    "media_players": "🎵",
    "maintenance": "🔧",
    "energy": "⚡",
    "persons": "👥",
    "repairs": "🩹",
    "updates": "🆙",
}
SUMMARY_PATHS = {
    "light": "light",
    "climate": "climate",
    "security": "security",
    "media_players": "media-players",
    "maintenance": "maintenance",
}


def summary_filters(hass: Hass, summary: str) -> list[EntityFilter]:
    f = make_filter
    if summary == "light":
        return [f(hass, domain="light", entity_category="none")]
    if summary == "climate":
        return [
            f(hass, domain="climate", entity_category="none"),
            f(hass, domain="humidifier", entity_category="none"),
            f(hass, domain="fan", entity_category="none"),
            f(hass, domain="water_heater", entity_category="none"),
            f(
                hass,
                domain="cover",
                device_class=["awning", "blind", "curtain", "shade", "shutter", "window", "none"],
                entity_category="none",
            ),
            f(hass, domain="binary_sensor", device_class=["window"], entity_category="none"),
        ]
    if summary == "security":
        return [
            f(hass, domain="camera", entity_category="none"),
            f(hass, domain="alarm_control_panel", entity_category="none"),
            f(hass, domain="lock", entity_category="none"),
            f(hass, domain="cover", device_class=["door", "garage", "gate", "window"], entity_category="none"),
            f(
                hass,
                domain="binary_sensor",
                device_class=[
                    "lock",
                    "door",
                    "window",
                    "garage_door",
                    "opening",
                    "carbon_monoxide",
                    "gas",
                    "moisture",
                    "safety",
                    "smoke",
                    "tamper",
                ],
                entity_category="none",
            ),
            f(hass, domain="binary_sensor", device_class=["tamper"], entity_category="diagnostic"),
        ]
    if summary == "media_players":
        return [f(hass, domain="media_player", entity_category="none")]
    if summary == "maintenance":
        return [
            f(hass, domain="sensor", device_class=["battery"]),
            f(hass, domain="binary_sensor", device_class=["battery"]),
        ]
    if summary == "persons":
        return [f(hass, domain="person")]
    return []


def summary_members(hass: Hass, summary: str) -> list[str]:
    if summary == "climate":
        return [a["temperature_entity_id"] for a in hass.areas.values() if a.get("temperature_entity_id")]
    if summary == "updates":
        return [e for e in hass.states if domain_of(e) == "update"]
    return find_entities(hass, summary_filters(hass, summary))


def _state(hass: Hass, eid: str) -> str:
    return str((hass.states.get(eid) or {}).get("state", ""))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def low_battery(hass: Hass, entity_ids: list[str]) -> list[str]:
    out = []
    for eid in entity_ids:
        state = _state(hass, eid)
        if domain_of(eid) == "binary_sensor":
            if state == "on":
                out.append(eid)
            continue
        entry = hass.entities.get(eid)
        if entry and entry.device_id:
            charging = next(
                (
                    e.entity_id
                    for e in hass.entities.values()
                    if e.device_id == entry.device_id
                    and domain_of(e.entity_id) == "binary_sensor"
                    and (hass.states.get(e.entity_id) or {}).get("attributes", {}).get("device_class")
                    == "battery_charging"
                ),
                None,
            )
            if charging and _state(hass, charging) == "on":
                continue
        try:
            if float(state) <= LOW_BATTERY_THRESHOLD:
                out.append(eid)
        except ValueError:
            pass
    return out


def summary_text(hass: Hass, summary: str, members: list[str]) -> tuple[str, bool]:
    """(text, needs attention) — hui-home-summary-card _computeSummaryState."""
    if summary == "light":
        on = sum(1 for e in members if _state(hass, e) == "on")
        return (_plural(on, "light on", "lights on"), True) if on else ("All lights off", False)
    if summary == "climate":
        values = []
        for e in members:
            with contextlib.suppress(ValueError):
                values.append(float(_state(hass, e)))
        if not values:
            return "", False
        lo, hi = f"{min(values):.1f}", f"{max(values):.1f}"
        return (f"{lo}°" if lo == hi else f"{lo} - {hi}°"), False
    if summary == "security":
        locks = [e for e in members if domain_of(e) == "lock"]
        alarms = [e for e in members if domain_of(e) == "alarm_control_panel"]
        if not locks and not alarms:
            return "", False
        unlocked = [e for e in locks if _state(hass, e) in ("unlocked", "jammed", "open")]
        if unlocked:
            return _plural(len(unlocked), "lock unlocked", "locks unlocked"), True
        disarmed = [e for e in alarms if _state(hass, e) == "disarmed"]
        if disarmed:
            return _plural(len(disarmed), "alarm disarmed", "alarms disarmed"), True
        return "All secure", False
    if summary == "media_players":
        playing = sum(1 for e in members if _state(hass, e) == "playing")
        return (_plural(playing, "playing", "playing"), True) if playing else ("Nothing playing", False)
    if summary == "maintenance":
        low = low_battery(hass, members)
        unavailable = [e for e in members if _state(hass, e) == "unavailable"]
        parts = []
        if low:
            parts.append(_plural(len(low), "low battery", "low batteries"))
        if unavailable:
            parts.append(_plural(len(unavailable), "battery unavailable", "batteries unavailable"))
        return (", ".join(parts), True) if parts else ("All good", False)
    if summary == "updates":
        pending = sum(1 for e in members if _state(hass, e) == "on")
        return (_plural(pending, "update available", "updates available"), True) if pending else ("Up to date", False)
    if summary == "repairs":
        return (_plural(hass.repairs_count, "repair", "repairs"), True) if hass.repairs_count else ("No repairs", False)
    if summary == "persons":
        home = sum(1 for e in members if _state(hass, e) == "home")
        return f"{home} of {len(members)} home", False
    return "", False


def area_members(hass: Hass, area_id: str) -> list[str]:
    area = hass.areas.get(area_id, {})
    members = [area[k] for k in ("temperature_entity_id", "humidity_entity_id") if area.get(k)]
    members += find_entities(hass, [make_filter(hass, domain="light", area=area_id, entity_category="none")])
    return members


def area_text(hass: Hass, area_id: str, members: list[str]) -> str:
    """Compact area card: temperature (and humidity) plus how many lights are on."""
    area = hass.areas.get(area_id, {})
    parts = []
    for key in ("temperature_entity_id", "humidity_entity_id"):
        eid = area.get(key)
        if eid and eid in hass.states and _state(hass, eid) not in ("unknown", "unavailable"):
            st = hass.states[eid]
            try:
                value = f"{float(st['state']):.1f}"
            except ValueError:
                value = st["state"]
            parts.append(f"{value}{st.get('attributes', {}).get('unit_of_measurement', '')}")
    lights_on = sum(1 for e in members if domain_of(e) == "light" and _state(hass, e) == "on")
    if lights_on:
        parts.append(f"💡 {lights_on}")
    return "  ".join(parts)


# ---------------------------------------------------------------------- #
# floor hierarchy and generic "by area" panels
# ---------------------------------------------------------------------- #
@dataclass
class Hierarchy:
    floors: list[tuple[str, list[str]]]
    areas: list[str]

    @property
    def floor_count(self) -> int:
        return len(self.floors) + (1 if self.areas else 0)


def floor_hierarchy(hass: Hass) -> Hierarchy:
    by_floor: dict[str, list[str]] = {}
    unassigned: list[str] = []
    for area in hass.areas.values():
        if area.get("floor_id"):
            by_floor.setdefault(area["floor_id"], []).append(area["area_id"])
        else:
            unassigned.append(area["area_id"])
    return Hierarchy([(fid, by_floor.get(fid, [])) for fid in hass.floors], unassigned)


def tile(hass: Hass, eid: str, prefix: str = "") -> Config:
    """computeAreaTileCardConfig: the name drops the area prefix when there is one."""
    return {"type": "tile", "entity": eid, "name": strip_prefix(hass.name(eid), prefix) if prefix else None}


def by_area_view(
    hass: Hass,
    entities: list[str],
    area_lights_toggle: bool = False,
    first_per_area: Callable[[str], list[str]] | None = None,
) -> list[Config]:
    """Sections grouped by floor then area, unassigned last (light/climate/security/media panels)."""
    hierarchy = floor_hierarchy(hass)
    sections: list[Config] = []

    def area_cards(area_ids: list[str], floor_name: str | None) -> list[Config]:
        cards: list[Config] = []
        for area_id in area_ids:
            area = hass.areas.get(area_id)
            if area is None:
                continue
            in_area = [e for e in entities if hass.area_id(e) == area_id]
            extra = [e for e in (first_per_area(area_id) if first_per_area else []) if e not in in_area]
            if not in_area:
                continue
            title = f"{floor_name} › {area['name']}" if floor_name else area["name"]
            cards.append({"type": "heading", "heading": title, "navigation_path": f"areas-{area_id}"})
            if area_lights_toggle:
                cards.append({"type": "ha-tui:area-lights", "area": area_id})
            cards += [tile(hass, e) for e in extra + in_area]
        return cards

    for floor_id, area_ids in hierarchy.floors:
        floor_name = hass.floors[floor_id]["name"] if hierarchy.floor_count > 1 else None
        if cards := area_cards(area_ids, floor_name):
            sections.append({"type": "grid", "cards": cards})
    if hierarchy.areas and (cards := area_cards(hierarchy.areas, "Other areas" if hierarchy.floor_count > 1 else None)):
        sections.append({"type": "grid", "cards": cards})
    unassigned = [e for e in entities if hass.area_id(e) is None]
    if unassigned:
        sections.append(
            {
                "type": "grid",
                "cards": [{"type": "heading", "heading": "Unassigned"}, *[tile(hass, e) for e in unassigned]],
            }
        )
    return sections


def summary_view(hass: Hass, summary: str) -> Config:
    title = SUMMARY_LABELS[summary]
    entities = find_entities(hass, summary_filters(hass, summary))
    if summary == "maintenance":

        def level(e: str) -> float:
            try:
                return float(_state(hass, e))
            except ValueError:
                return -1 if _state(hass, e) == "on" else 101

        low = low_battery(hass, entities)
        unavailable = [e for e in entities if _state(hass, e) == "unavailable"]
        rest = sorted((e for e in entities if e not in low and e not in unavailable), key=level)
        sections = []
        for heading, ids in (
            ("Low battery", sorted(low, key=level)),
            ("Unavailable", unavailable),
            ("Batteries", rest),
        ):
            if ids:
                sections.append(
                    {"type": "grid", "cards": [{"type": "heading", "heading": heading}, *[tile(hass, e) for e in ids]]}
                )
        return {"title": title, "type": "sections", "sections": sections}
    first = None
    if summary == "climate":

        def first(area_id: str) -> list[str]:
            area = hass.areas.get(area_id, {})
            return [area[k] for k in ("temperature_entity_id", "humidity_entity_id") if area.get(k)]

    sections = by_area_view(hass, entities, area_lights_toggle=summary == "light", first_per_area=first)
    return {"title": title, "type": "sections", "sections": sections}


# ---------------------------------------------------------------------- #
# home-overview / home-area / home-other-devices
# ---------------------------------------------------------------------- #
OTHER_DEVICES_HIDDEN_PLATFORMS = ["automation", "script", "hassio", "backup", "mobile_app", "zone", "person"]
OTHER_DEVICES_HIDDEN_DOMAINS = [
    "ai_task",
    "automation",
    "configurator",
    "device_tracker",
    "event",
    "geo_location",
    "notify",
    "persistent_notification",
    "script",
    "sun",
    "tag",
    "todo",
    "zone",
    *ASSIST_ENTITIES,
]


def other_device_entities(hass: Hass) -> dict[str, list[str]]:
    flt = make_filter(
        hass, area=None, hidden_platform=OTHER_DEVICES_HIDDEN_PLATFORMS, hidden_domains=OTHER_DEVICES_HIDDEN_DOMAINS
    )
    primary = make_filter(hass, entity_category="none")
    by_device: dict[str, list[str]] = {}
    for eid in hass.states:
        entry = hass.entities.get(eid)
        if (
            not (flt(eid) and primary(eid))
            or entry is None
            or not entry.device_id
            or entry.device_id not in hass.devices
        ):
            continue
        by_device.setdefault(entry.device_id, []).append(eid)
    return by_device


def generate_home_overview(hass: Hass, config: Config) -> Config:
    hierarchy = floor_hierarchy(hass)
    sections: list[Config] = []

    favorites = [e for e in config.get("favorite_entities") or [] if e in hass.states]
    limit = max(8, len(favorites))
    if not config.get("hide_suggested_entities"):
        predicted = [
            e
            for e in hass.common_controls
            if e in hass.states and not (hass.entities.get(e) and hass.entities[e].hidden) and e not in favorites
        ]
        suggested = (favorites + predicted)[:limit]
    else:
        suggested = favorites
    if suggested:
        sections.append(
            {
                "type": "grid",
                "cards": [{"type": "heading", "heading": "Favorites"}, *[tile(hass, e) for e in suggested]],
            }
        )

    summary_cards: list[Config] = []
    if hass.is_admin:
        summary_cards += [{"type": "repairs", "hide_empty": True}, {"type": "updates", "hide_empty": True}]
    for item in resolve_shortcuts(config.get("shortcuts")):
        if item.get("type") != "summary":
            if item.get("path"):
                summary_cards.append(
                    {
                        "type": "ha-tui:navigate",
                        "name": item.get("label") or item["path"],
                        "navigation_path": item["path"],
                    }
                )
            continue
        key = item["key"]
        if item.get("hidden"):
            continue
        if key == "weather":
            weather = sorted(e for e in hass.states if make_filter(hass, domain="weather", entity_category="none")(e))
            if weather:
                summary_cards.append({"type": "tile", "entity": weather[0], "name": "Weather"})
            continue
        if key == "energy":
            continue  # needs the energy statistics collection; not ported
        if key in ("light", "climate", "security", "maintenance") and key not in hass.panels:
            continue
        # hasClimateEntities: an area temperature/humidity sensor, or any climate-type entity.
        has = bool(summary_members(hass, key))
        if key == "climate":
            has = has or bool(find_entities(hass, summary_filters(hass, key)))
        if has:
            summary_cards.append({"type": "home-summary", "summary": key, "navigation_path": SUMMARY_PATHS.get(key)})
    if summary_cards:
        sections.append({"type": "grid", "cards": [{"type": "heading", "heading": "Summaries"}, *summary_cards]})

    for floor_id, area_ids in hierarchy.floors:
        if area_ids:
            floor = hass.floors[floor_id]
            heading = floor["name"] if hierarchy.floor_count > 1 else "Areas"
            cards = [{"type": "area", "area": a, "navigation_path": f"areas-{a}"} for a in area_ids]
            sections.append({"type": "grid", "cards": [{"type": "heading", "heading": heading}, *cards]})

    has_other_devices = bool(other_device_entities(hass))
    if hierarchy.areas or has_other_devices:
        cards = [{"type": "area", "area": a, "navigation_path": f"areas-{a}"} for a in hierarchy.areas]
        if has_other_devices:
            cards.append({"type": "ha-tui:navigate", "name": "Devices", "navigation_path": "other-devices"})
        if not hierarchy.floors and not hierarchy.areas:
            heading = None
        elif not hierarchy.floors:
            heading = "Areas"
        elif not hierarchy.areas:
            heading = "Devices"
        else:
            heading = "Other areas"
        sections.append(
            {"type": "grid", "cards": ([{"type": "heading", "heading": heading}] if heading else []) + cards}
        )

    view: Config = {"title": "Home", "type": "sections", "sections": sections}
    if not config.get("hide_welcome_message") and hass.user_name:
        view["header"] = f"Welcome {hass.user_name}"
    return view


def generate_home_area(hass: Hass, config: Config) -> Config:
    area_id = config["area"]
    area = hass.areas.get(area_id)
    if area is None:
        return {"cards": []}
    badges = [
        {"type": "entity", "entity": area[k]} for k in ("temperature_entity_id", "humidity_entity_id") if area.get(k)
    ]
    in_area = [e for e in hass.states if make_filter(hass, area=area_id)(e)]
    by_summary = {
        s: find_entities(hass, summary_filters(hass, s), among=in_area)
        for s in ("light", "climate", "security", "media_players", "maintenance", "persons")
    }
    name = area["name"]
    sections: list[Config] = []

    def section(heading: str, ids: list[str], extra: list[Config] | None = None, nav: str | None = None) -> None:
        if ids:
            head: Config = {"type": "heading", "heading": heading}
            if nav:
                head["navigation_path"] = nav
            sections.append({"type": "grid", "cards": [head, *(extra or []), *[tile(hass, e, name) for e in ids]]})

    section("Lights", by_summary["light"], [{"type": "ha-tui:area-lights", "area": area_id}], "light")
    section("Climate", by_summary["climate"], nav="climate")
    section("Security", by_summary["security"], nav="security")
    section("Media players", by_summary["media_players"], nav="media-players")

    scenes = [e for e in in_area if make_filter(hass, domain="scene", entity_category="none")(e)]
    section("Scenes", scenes)
    automations = [e for e in in_area if make_filter(hass, domain="automation", entity_category="none")(e)]
    summary_entities = {e for s, ids in by_summary.items() if s != "maintenance" for e in ids}
    others = [e for e in in_area if e not in summary_entities and e not in scenes and e not in automations]

    by_device: dict[str, list[str]] = {}
    for eid in others:
        entry = hass.entities.get(eid)
        key = entry.device_id if entry and entry.device_id and entry.device_id in hass.devices else "unassigned"
        by_device.setdefault(key, []).append(eid)
    if "unassigned" in by_device:
        by_device["unassigned"] = by_device.pop("unassigned")  # unassigned goes last
    battery = make_filter(hass, domain="sensor", device_class="battery")
    primary = make_filter(hass, entity_category="none")
    for device_id, ids in by_device.items():
        shown = [e for e in ids if not battery(e) and primary(e)]
        if not shown:
            continue
        heading = hass.device_name(device_id) if device_id != "unassigned" else "Others"
        batteries = [e for e in ids if battery(e)][:1]
        tiles = [{"type": "tile", "entity": e, "name": hass.entity_only_name(e)} for e in shown]
        sections.append(
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": heading},
                    *tiles,
                    *[{"type": "tile", "entity": b, "name": "Battery"} for b in batteries],
                ],
            }
        )

    section("Automations", automations)
    return {"type": "sections", "sections": sections, "badges": badges}


def generate_other_devices(hass: Hass) -> Config:
    sections = []
    for device_id, ids in other_device_entities(hass).items():
        sections.append(
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": hass.device_name(device_id)},
                    {"type": "entities", "entities": [{"entity": e, "name": hass.entity_only_name(e)} for e in ids]},
                ],
            }
        )
    return {"title": "Devices", "type": "sections", "sections": sections}


DEFAULT_SUMMARY_KEYS = ["light", "climate", "security", "media_players", "maintenance", "weather", "energy"]


def resolve_shortcuts(saved: list[Config] | None) -> list[Config]:
    out: list[Config] = []
    seen: set[str] = set()
    for item in saved or []:
        if item.get("type") == "summary":
            if item.get("key") not in DEFAULT_SUMMARY_KEYS or item["key"] in seen:
                continue
            seen.add(item["key"])
        out.append(item)
    out += [{"type": "summary", "key": k} for k in DEFAULT_SUMMARY_KEYS if k not in seen]
    return out


def generate_home_dashboard(config: Config, hass: Hass) -> Config:
    views: list[Config] = [{"title": "Home", "path": "overview", "strategy": {**config, "type": "home-overview"}}]
    for area in hass.areas.values():
        views.append(
            {
                "title": area["name"],
                "path": f"areas-{area['area_id']}",
                "subview": True,
                "strategy": {"type": "home-area", "area": area["area_id"]},
            }
        )
    views.append(
        {
            "title": "Media players",
            "path": "media-players",
            "subview": True,
            "strategy": {"type": "home-summary-view", "summary": "media_players"},
        }
    )
    views.append(
        {"title": "Devices", "path": "other-devices", "subview": True, "strategy": {"type": "home-other-devices"}}
    )
    for summary in ("light", "climate", "security", "maintenance"):
        if summary in hass.panels:
            views.append(
                {
                    "title": SUMMARY_LABELS[summary],
                    "path": SUMMARY_PATHS[summary],
                    "subview": True,
                    "strategy": {"type": "home-summary-view", "summary": summary},
                }
            )
    return {"title": "Home", "views": views}


def generate_home_view(strategy: Config, hass: Hass) -> Config | None:
    stype = strategy.get("type")
    if stype == "home-overview":
        return generate_home_overview(hass, strategy)
    if stype == "home-area":
        return generate_home_area(hass, strategy)
    if stype == "home-other-devices":
        return generate_other_devices(hass)
    if stype == "home-summary-view":
        return summary_view(hass, strategy["summary"])
    return None
