"""Terminal-friendly model of a resolved Lovelace dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Item:
    """One row of a card.

    kind:
      entity    an entity row (entity_id, optional display name override)
      area        an area summary that navigates to `path`
      summary     a home summary (`key`: light, climate, security...) computed from `members`
      area_lights turn all lights of `area_id` on/off
      navigate    a plain link to `path`
      markdown    markdown text; rendered server-side if it contains a template

    `members` lists the entities a computed row depends on, for live updates.
    """

    kind: str = "entity"
    entity_id: str | None = None
    name: str | None = None
    area_id: str | None = None
    path: str | None = None
    text: str | None = None
    key: str | None = None
    members: list[str] = field(default_factory=list)


@dataclass
class Card:
    title: str | None = None
    items: list[Item] = field(default_factory=list)


@dataclass
class View:
    title: str
    path: str
    icon: str | None = None
    cards: list[Card] = field(default_factory=list)
    badges: list[Item] = field(default_factory=list)
    subview: bool = False
    error: str | None = None
    header: str | None = None

    def entity_ids(self) -> list[str]:
        ids = [i.entity_id for c in self.cards for i in c.items if i.entity_id]
        ids += [b.entity_id for b in self.badges if b.entity_id]
        return list(dict.fromkeys(ids))


@dataclass
class Dashboard:
    url_path: str | None
    title: str
    views: list[View] = field(default_factory=list)
    strategy: str | None = None
    notice: str | None = None

    def view_by_path(self, path: str) -> View | None:
        path = path.rstrip("/").rsplit("/", 1)[-1]
        return next((v for v in self.views if v.path == path), None)
