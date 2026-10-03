"""Turn raw Lovelace configs (stored or strategy-generated) into the terminal model.

The browser renders arbitrary card trees, including custom cards it loads
as JS. We can't run those, so instead we walk the card tree and keep what
matters in a terminal: which entities are shown, in what order, under which
headings. Headings come from card titles, `heading` cards, `section` rows,
and title-only custom cards (e.g. a button-card with just a name).
"""

from __future__ import annotations

import re
from typing import Any

from ..client import HAClient, HACommandError
from ..hass import Hass
from .home import area_members, summary_members, summary_text
from .model import Card, Dashboard, Item, View
from .strategies import StrategyError, generate_dashboard, generate_view

Config = dict[str, Any]

ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# custom:button-card JS templates and HA Jinja templates can't be shown verbatim.
_JS_TEMPLATE = "[[["


def _plain(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and _JS_TEMPLATE not in value:
        return value.strip()
    return None


class _Builder:
    def __init__(self, hass: Hass) -> None:
        self.hass = hass
        self.cards: list[Card] = []
        self.current: Card | None = None

    def start(self, title: str | None = None) -> None:
        self.current = Card(title=title)
        self.cards.append(self.current)

    def add(self, item: Item) -> None:
        if self.current is None:
            self.start()
        assert self.current is not None
        self.current.items.append(item)

    def add_entity(self, entity_id: Any, name: Any = None) -> None:
        if isinstance(entity_id, str) and ENTITY_ID_RE.match(entity_id):
            self.add(Item(entity_id=entity_id, name=_plain(name)))

    def add_entity_rows(self, rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, str):
                self.add_entity(row)
            elif isinstance(row, dict):
                if row.get("type") == "section" and (label := _plain(row.get("label"))):
                    self.start(label)
                elif "entity" in row:
                    self.add_entity(row["entity"], row.get("name"))
                elif isinstance(row.get("row"), dict):
                    self.add_entity_rows([row["row"]])
                elif isinstance(row.get("entities"), list):
                    self.add_entity_rows(row["entities"])

    def walk(self, card: Any) -> None:
        if not isinstance(card, dict):
            return
        ctype = str(card.get("type", ""))

        if ctype == "heading":
            self.start(_plain(card.get("heading")))
            return
        if ctype == "markdown":
            content = card.get("content")
            if isinstance(content, str) and content.strip():
                self.start(_plain(card.get("title")))
                self.add(Item(kind="markdown", text=content))
                self.current = None
            return
        if ctype == "area":
            area_id = card.get("area")
            if area_id in self.hass.areas:
                self.add(
                    Item(
                        kind="area",
                        area_id=area_id,
                        path=card.get("navigation_path"),
                        name=_plain(card.get("name")),
                        members=area_members(self.hass, area_id),
                    )
                )
            return
        if ctype in ("home-summary", "repairs", "updates"):
            key = card.get("summary") or ctype
            members = summary_members(self.hass, key)
            if card.get("hide_empty") and not summary_text(self.hass, key, members)[1]:
                return
            self.add(Item(kind="summary", key=key, path=card.get("navigation_path"), members=members))
            return
        if ctype == "ha-tui:area-lights":
            lights = [m for m in area_members(self.hass, card["area"]) if m.startswith("light.")]
            if lights:
                self.add(Item(kind="area_lights", area_id=card["area"], members=lights))
            return
        if ctype == "ha-tui:navigate":
            self.add(Item(kind="navigate", name=_plain(card.get("name")), path=card.get("navigation_path")))
            return
        if ctype == "map":
            self.add_entity_rows(card.get("entities"))
            if card.get("show_all") or not card.get("entities"):
                for eid, state in self.hass.states.items():
                    attrs = state.get("attributes", {})
                    if "latitude" in attrs and "longitude" in attrs:
                        self.add_entity(eid)
            return

        title = _plain(card.get("title"))
        entity = card.get("entity")
        nested = card.get("cards")
        if title and (isinstance(nested, list) or "entities" in card):
            self.start(title)
        elif ctype == "custom:button-card" and not entity and not nested:
            # Title-only button-cards are how many hand-built dashboards draw headers.
            heading = _plain(card.get("name")) or _plain((card.get("variables") or {}).get("name"))
            if heading:
                self.start(heading)
            return

        if isinstance(nested, list):
            for sub in nested:
                self.walk(sub)
        if isinstance(card.get("card"), dict):
            self.walk(card["card"])
        self.add_entity_rows(card.get("entities"))
        if entity is not None:
            self.add_entity(entity, card.get("name"))
        for key in ("image_entity", "camera_image"):
            self.add_entity(card.get(key))
        if isinstance(card.get("elements"), list):
            for element in card["elements"]:
                if isinstance(element, dict):
                    self.add_entity(element.get("entity"), element.get("title"))
        for key in ("header", "footer"):
            if isinstance(card.get(key), dict):
                self.add_entity_rows(card[key].get("entities"))
        if ctype.startswith("custom:") and isinstance(card.get("variables"), list):
            # decluttering-card style: entity ids passed as template variables.
            for var in card["variables"]:
                if isinstance(var, dict):
                    for value in var.values():
                        if value in self.hass.states:
                            self.add_entity(value)


def convert_view(raw: Config, hass: Hass, index: int) -> View:
    builder = _Builder(hass)
    if isinstance(raw.get("sections"), list):
        for section in raw["sections"]:
            builder.current = None
            if isinstance(section, dict):
                for card in section.get("cards") or []:
                    builder.walk(card)
    for card in raw.get("cards") or []:
        builder.current = None
        builder.walk(card)

    cards: list[Card] = []
    for card in builder.cards:
        # de-duplicate entities within a card, keep first occurrence
        seen: set[str] = set()
        items = []
        for item in card.items:
            if item.kind == "entity":
                if item.entity_id in seen:
                    continue
                seen.add(item.entity_id)  # type: ignore[arg-type]
            items.append(item)
        if items:
            cards.append(Card(title=card.title, items=items))

    badges = []
    for badge in raw.get("badges") or []:
        if isinstance(badge, str):
            badges.append(Item(entity_id=badge))
        elif isinstance(badge, dict) and isinstance(badge.get("entity"), str):
            badges.append(Item(entity_id=badge["entity"], name=_plain(badge.get("name"))))

    return View(
        title=_plain(raw.get("title")) or str(raw.get("path") or f"View {index + 1}"),
        path=str(raw.get("path") or index),
        icon=raw.get("icon"),
        cards=cards,
        badges=badges,
        subview=bool(raw.get("subview")),
        header=raw.get("header") if isinstance(raw.get("header"), str) else None,
    )


def build_dashboard(raw: Config, hass: Hass, url_path: str | None, title: str) -> Dashboard:
    notice = None
    strategy = None
    if isinstance(raw.get("strategy"), dict):
        strategy = raw["strategy"].get("type")
        raw, notice = generate_dashboard(raw["strategy"], hass)

    views = []
    for index, raw_view in enumerate(raw.get("views") or []):
        if not isinstance(raw_view, dict):
            continue
        error = None
        if isinstance(raw_view.get("strategy"), dict):
            try:
                generated = generate_view(raw_view["strategy"], hass)
            except StrategyError as exc:
                generated, error = {}, str(exc)
            # The view's own keys (title, path, subview...) win over generated ones.
            raw_view = {**generated, **{k: v for k, v in raw_view.items() if k != "strategy"}}
        view = convert_view(raw_view, hass, index)
        view.error = error
        views.append(view)
    return Dashboard(url_path=url_path, title=title, views=views, strategy=strategy, notice=notice)


HOME = "home"


async def list_dashboards(client: HAClient) -> list[tuple[str | None, str]]:
    """(url_path, title) for every dashboard: the Home panel (new Overview) first if present."""
    out: list[tuple[str | None, str]] = []
    if HOME in (await client.send("get_panels") or {}):
        out.append((HOME, "Home (new Overview)"))
    out.append((None, "Overview"))
    for dash in await client.list_dashboards():
        if dash.get("mode") == "yaml" and not dash.get("url_path"):
            continue
        out.append((dash["url_path"], dash.get("title") or dash["url_path"]))
    return out


def default_dashboard(hass: Hass) -> str | None:
    """The new Overview (Home panel) when the instance has it, else the default Lovelace dashboard."""
    return HOME if HOME in hass.panels else None


async def load_home_context(client: HAClient, hass: Hass) -> dict:
    """What the Home panel fetches besides the registries: its config, predictions, repairs."""
    try:
        config = (await client.send("frontend/get_system_data", key="home") or {}).get("value") or {}
    except HACommandError:
        config = {}
    try:
        hass.common_controls = (await client.send("usage_prediction/common_control")).get("entities", [])
    except HACommandError:
        hass.common_controls = []
    if hass.is_admin:
        try:
            issues = (await client.send("repairs/list_issues")).get("issues", [])
            hass.repairs_count = sum(1 for i in issues if not i.get("ignored") and not i.get("dismissed_version"))
        except HACommandError:
            hass.repairs_count = 0
    return config


async def load_dashboard(client: HAClient, hass: Hass, url_path: str | None, title: str | None = None) -> Dashboard:
    if url_path == HOME:
        config = await load_home_context(client, hass)
        raw = {"strategy": {**config, "type": "home", "home_panel": True}}
        return build_dashboard(raw, hass, HOME, title or "Home")
    try:
        raw = await client.lovelace_config(url_path)
    except HACommandError as exc:
        if exc.code != "config_not_found":
            raise
        # No stored config: the browser falls back to the auto-generated dashboard.
        raw = {"strategy": {"type": "original-states"}}
    return build_dashboard(raw, hass, url_path, title or raw.get("title") or url_path or "Overview")
