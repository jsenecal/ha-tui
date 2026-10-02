"""Python ports of the HA frontend's dashboard/view strategies.

Generated dashboards (the auto-generated default "Overview", the Areas
dashboard, the Map dashboard...) are never stored server-side: the browser
builds their Lovelace config from the registries every time it renders them.
These functions mirror that code (home-assistant/frontend,
src/panels/lovelace/strategies and common/generate-lovelace-config.ts) and
return raw Lovelace config dicts, which then go through the same card
converter as user-written dashboards.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..format import domain_of
from ..hass import Hass

Config = dict[str, Any]

HIDE_DOMAIN = {
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
    "assist_satellite",
    "conversation",
    "stt",
    "tts",
}
HIDE_PLATFORM = {"backup", "mobile_app"}
HELPER_DOMAINS = [
    "input_boolean",
    "input_button",
    "input_text",
    "input_number",
    "input_datetime",
    "input_select",
    "counter",
    "timer",
    "schedule",
]
SENSOR_ENTITIES = {"sensor", "binary_sensor", "calendar", "camera", "device_tracker", "image", "weather"}


class StrategyError(RuntimeError):
    pass


def domain_title(domain: str) -> str:
    return domain.replace("_", " ").capitalize()


def _sort_key(text: str) -> str:
    return text.casefold()


def order_sorted(items: Iterable[Any], order: list[str] | None, key: Callable[[Any], str] = lambda x: x) -> list[Any]:
    """frontend orderCompare: listed ids first in list order, the rest keep their order."""
    items = list(items)
    if not order:
        return items
    index = {v: i for i, v in enumerate(order)}
    return sorted(items, key=lambda x: index.get(key(x), len(order)))


def strip_prefix(name: str, prefix: str) -> str | None:
    lower_name, lower_prefix = name.lower(), prefix.lower()
    for suffix in (" ", ": ", " - "):
        if lower_name.startswith(lower_prefix + suffix):
            rest = name[len(lower_prefix + suffix) :]
            if rest:
                first_word = rest.split(" ", 1)[0] if " " in rest else ""
                return rest if first_word.lower() != first_word else rest[0].upper() + rest[1:]
    return None


# ---------------------------------------------------------------------- #
# original-states (the auto-generated default dashboard)
# ---------------------------------------------------------------------- #
def compute_cards(hass: Hass, entity_ids: list[str], title: str | None, footer: bool = True) -> list[Config]:
    cards: list[Config] = []
    rows: list[str | Config] = []
    footer_entities: list[Config] = []
    prefix = title.lower() if title else None

    for eid in entity_ids:
        domain = domain_of(eid)
        if domain == "alarm_control_panel":
            cards.append({"type": "alarm-panel", "entity": eid})
        elif domain == "camera":
            cards.append({"type": "picture-entity", "entity": eid})
        elif domain == "image":
            cards.append({"type": "picture", "image_entity": eid})
        elif domain == "climate":
            cards.append({"type": "thermostat", "entity": eid})
        elif domain == "humidifier":
            cards.append({"type": "humidifier", "entity": eid})
        elif domain == "media_player":
            cards.append({"type": "media-control", "entity": eid})
        elif domain == "plant":
            cards.append({"type": "plant-status", "entity": eid})
        elif domain == "weather":
            cards.append({"type": "weather-forecast", "entity": eid, "show_forecast": False})
        elif footer and domain in ("scene", "script"):
            conf: Config = {"entity": eid}
            if prefix and eid in hass.states and (name := strip_prefix(hass.name(eid), prefix)):
                conf["name"] = name
            footer_entities.append(conf)
        else:
            if prefix and eid in hass.states and (name := strip_prefix(hass.name(eid), prefix)):
                rows.append({"entity": eid, "name": name})
            else:
                rows.append(eid)

    def row_key(row: str | Config) -> tuple[int, str]:
        eid = row if isinstance(row, str) else row["entity"]
        is_sensor = domain_of(eid) in SENSOR_ENTITIES
        name = row.get("name") if isinstance(row, dict) else None
        return (1 if is_sensor else 0, _sort_key(name or (hass.name(eid) if eid in hass.states else "")))

    rows.sort(key=row_key)

    if not rows and footer_entities:
        return compute_cards(hass, entity_ids, title, footer=False)

    if rows or footer_entities:
        card: Config = {"type": "entities", "entities": rows}
        if title:
            card["title"] = title
        if footer_entities:
            card["footer"] = {"type": "buttons", "entities": footer_entities}
        cards.insert(0, card)

    if len(cards) < 2:
        return cards
    return [{"type": "grid", "square": False, "columns": 1, "cards": cards}]


def _default_view_states(hass: Hass) -> dict[str, dict[str, Any]]:
    hidden = {
        e.entity_id
        for e in hass.entities.values()
        if e.entity_category or (e.platform and e.platform in HIDE_PLATFORM) or e.hidden
    }
    return {eid: s for eid, s in hass.states.items() if domain_of(eid) not in HIDE_DOMAIN and eid not in hidden}


def _generate_view_config(hass: Hass, path: str, title: str, entities: dict[str, dict[str, Any]]) -> Config:
    by_domain: dict[str, list[str]] = {}
    for eid in entities:
        by_domain.setdefault(domain_of(eid), []).append(eid)

    cards: list[Config] = []
    if persons := by_domain.pop("person", None):
        if len(persons) == 1:
            cards.append({"type": "entities", "entities": persons})
        else:
            cards.append(
                {
                    "type": "grid",
                    "square": True,
                    "columns": 3,
                    "cards": [{"type": "picture-entity", "entity": p} for p in persons],
                }
            )

    helpers: list[str] = []
    for domain in HELPER_DOMAINS:
        helpers += by_domain.pop(domain, [])
    titles = {d: domain_title(d) for d in by_domain}
    if helpers:
        by_domain["_helpers"] = helpers
        titles["_helpers"] = "Helpers"

    for domain in sorted(by_domain, key=lambda d: _sort_key(titles[d])):
        ids = sorted(by_domain[domain], key=lambda e: _sort_key(hass.name(e)))
        cards += compute_cards(hass, ids, titles[domain])

    return {"path": path, "title": title, "cards": cards}


def generate_default_view(hass: Hass, config: Config) -> Config:
    states = _default_view_states(hass)
    areas_prefs = config.get("areas") or {}

    areas_with_entities: dict[str, list[str]] = {}
    devices_with_entities: dict[str, list[str]] = {}
    others = dict(states)
    for entry in hass.entities.values():
        eid = entry.entity_id
        device = hass.devices.get(entry.device_id) if entry.device_id else None
        area_id = entry.area_id or (device or {}).get("area_id")
        if area_id and area_id in hass.areas and eid in others:
            areas_with_entities.setdefault(area_id, []).append(eid)
            del others[eid]
        elif device is not None and eid in others:
            devices_with_entities.setdefault(entry.device_id, []).append(eid)
            del others[eid]
    for device_id, ids in list(devices_with_entities.items()):
        if len(ids) == 1:
            others[ids[0]] = states[ids[0]]
            del devices_with_entities[device_id]

    for area_id in areas_prefs.get("hidden") or []:
        areas_with_entities.pop(area_id, None)
    if config.get("hide_entities_without_area"):
        devices_with_entities, others = {}, {}

    groups = [s for eid, s in others.items() if domain_of(eid) == "group"]
    ungrouped = {eid: s for eid, s in others.items() if domain_of(eid) != "group"}
    for group in groups:
        for member in group.get("attributes", {}).get("entity_id", []) or []:
            ungrouped.pop(member, None)
    groups.sort(key=lambda g: g.get("attributes", {}).get("order") or 0)

    group_cards: list[Config] = []
    for group in groups:
        members = group.get("attributes", {}).get("entity_id", []) or []
        group_cards += compute_cards(hass, list(members), hass.name(group["entity_id"]))

    view = _generate_view_config(hass, "default_view", "Home", ungrouped)

    area_cards: list[Config] = []
    for area_id in order_sorted(hass.areas, areas_prefs.get("order")):
        if area_id in areas_with_entities:
            area_cards += compute_cards(hass, areas_with_entities[area_id], hass.areas[area_id]["name"])

    device_cards: list[Config] = []
    for device_id in sorted(devices_with_entities, key=lambda d: _sort_key(hass.device_name(d))):
        device_cards += compute_cards(hass, devices_with_entities[device_id], hass.device_name(device_id))

    view["cards"] = area_cards + group_cards + view["cards"] + device_cards
    return view


# ---------------------------------------------------------------------- #
# areas dashboard
# ---------------------------------------------------------------------- #
AREA_GROUPS: list[tuple[str, str, list[tuple[set[str], set[str] | None]]]] = [
    ("lights", "Lights", [({"light"}, None)]),
    ("covers", "Covers", [({"cover"}, None), ({"binary_sensor"}, {"door", "garage_door", "window"})]),
    ("climate", "Climate", [({"climate"}, None), ({"humidifier"}, None), ({"water_heater"}, None), ({"fan"}, None)]),
    ("media_players", "Media players", [({"media_player"}, None)]),
    ("security", "Security", [({"alarm_control_panel"}, None), ({"lock"}, None), ({"camera"}, None)]),
    ("actions", "Actions", [({"script", "scene"}, None), ({"automation"}, None)]),
    (
        "others",
        "Others",
        [
            ({"vacuum"}, None),
            ({"lawn_mower"}, None),
            ({"valve"}, None),
            ({"switch", "button", "input_boolean", "input_button"}, None),
            ({"select", "number", "input_select", "input_number", "counter", "timer"}, None),
        ],
    ),
]


def _matches(hass: Hass, eid: str, area_id: str, domains: set[str], device_classes: set[str] | None) -> bool:
    state = hass.states.get(eid)
    if state is None or domain_of(eid) not in domains:
        return False
    if device_classes is not None and (state.get("attributes", {}).get("device_class") or "none") not in device_classes:
        return False
    entry = hass.entities.get(eid)
    if entry is not None and entry.hidden:
        return False
    if hass.area_id(eid) != area_id:
        return False
    return (entry.entity_category if entry else None) is None


def area_grouped_entities(hass: Hass, area_id: str, groups_options: Config | None = None) -> dict[str, list[str]]:
    groups_options = groups_options or {}
    result: dict[str, list[str]] = {}
    for key, _title, filters in AREA_GROUPS:
        ids: list[str] = []
        for domains, device_classes in filters:
            ids += [eid for eid in hass.states if _matches(hass, eid, area_id, domains, device_classes)]
        opts = groups_options.get(key) or {}
        if hidden := set(opts.get("hidden") or []):
            ids = [e for e in ids if e not in hidden]
        result[key] = order_sorted(ids, opts.get("order"))
    return result


def area_path(area_id: str) -> str:
    return f"areas-{area_id}"


def _visible_areas(hass: Hass, display: Config | None) -> list[dict[str, Any]]:
    display = display or {}
    hidden = set(display.get("hidden") or [])
    areas = [a for a in hass.areas.values() if a["area_id"] not in hidden]
    return order_sorted(areas, display.get("order"), key=lambda a: a["area_id"])


def generate_area_view(hass: Hass, config: Config) -> Config:
    area_id = config.get("area")
    area = hass.areas.get(area_id or "")
    if area is None:
        raise StrategyError(f"Unknown area {area_id!r}")
    badges = [
        {"type": "entity", "entity": area[k]} for k in ("temperature_entity_id", "humidity_entity_id") if area.get(k)
    ]
    grouped = area_grouped_entities(hass, area_id, config.get("groups_options"))
    sections = []
    for key, title, _filters in AREA_GROUPS:
        if not grouped[key]:
            continue
        tiles = []
        for eid in grouped[key]:
            if domain_of(eid) == "camera":
                tiles.append({"type": "picture-entity", "entity": eid})
            else:
                tiles.append({"type": "tile", "entity": eid, "name": strip_prefix(hass.name(eid), area["name"])})
        sections.append({"type": "grid", "cards": [{"type": "heading", "heading": title}, *tiles]})
    return {"type": "sections", "sections": sections, "badges": badges}


def generate_areas_overview(hass: Hass, config: Config) -> Config:
    areas = _visible_areas(hass, config.get("areas_display"))
    floors = order_sorted(
        hass.floors.values(), (config.get("floors_display") or {}).get("order"), key=lambda f: f["floor_id"]
    )
    buckets = [(f["floor_id"], f["name"]) for f in floors] + [(None, "Other areas")]
    populated = [(fid, name, [a for a in areas if a.get("floor_id") == fid]) for fid, name in buckets]
    populated = [p for p in populated if p[2]]
    sections = []
    for floor_id, name, floor_areas in populated:
        heading = "Areas" if len(populated) == 1 and floor_id is None else name
        cards = [
            {"type": "area", "area": a["area_id"], "navigation_path": area_path(a["area_id"])} for a in floor_areas
        ]
        sections.append({"type": "grid", "cards": [{"type": "heading", "heading": heading}, *cards]})
    return {"type": "sections", "sections": sections}


# ---------------------------------------------------------------------- #
# dispatch
# ---------------------------------------------------------------------- #
def generate_dashboard(strategy: Config, hass: Hass) -> tuple[Config, str | None]:
    """Returns (raw lovelace config, optional notice for the user)."""
    stype = strategy.get("type", "")
    if stype == "original-states":
        return {"views": [{"strategy": strategy}]}, None
    if stype == "areas":
        views: list[Config] = [
            {
                "title": "Home",
                "path": "home",
                "strategy": {
                    "type": "areas-overview",
                    "areas_display": strategy.get("areas_display"),
                    "areas_options": strategy.get("areas_options"),
                    "floors_display": strategy.get("floors_display"),
                },
            }
        ]
        for area in _visible_areas(hass, strategy.get("areas_display")):
            opts = (strategy.get("areas_options") or {}).get(area["area_id"]) or {}
            views.append(
                {
                    "title": area["name"],
                    "path": area_path(area["area_id"]),
                    "subview": True,
                    "strategy": {"type": "area", "area": area["area_id"], "groups_options": opts.get("groups_options")},
                }
            )
        return {"views": views}, None
    if stype == "map":
        return {"views": [{"title": "Map", "path": "map", "strategy": {"type": "map"}}]}, None
    if stype == "iframe":
        return {
            "views": [{"title": "Web page", "cards": [{"type": "markdown", "content": strategy.get("url", "")}]}]
        }, None
    return (
        {"views": [{"strategy": {"type": "original-states"}}]},
        f"Dashboard strategy {stype!r} runs only in the browser; showing the auto-generated layout instead.",
    )


def generate_view(strategy: Config, hass: Hass) -> Config:
    stype = strategy.get("type", "")
    if stype == "original-states":
        return generate_default_view(hass, strategy)
    if stype == "areas-overview":
        return generate_areas_overview(hass, strategy)
    if stype == "area":
        return generate_area_view(hass, strategy)
    if stype == "map":
        return {"title": "Map", "cards": [{"type": "map", "show_all": True}]}
    raise StrategyError(f"View strategy {stype!r} runs only in the browser")
