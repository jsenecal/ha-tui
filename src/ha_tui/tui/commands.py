"""Command palette providers (ctrl+p or /)."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

from ..format import state_text

if TYPE_CHECKING:
    from .app import HATuiApp


class EntityCommands(Provider):
    """Fuzzy-find any entity and open its controls."""

    async def search(self, query: str) -> Hits:
        app: HATuiApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        hass = app.hass
        for eid in list(hass.states):
            text = f"{hass.name(eid)}  ·  {eid}"
            if (score := matcher.match(text)) > 0:
                yield Hit(
                    score,
                    matcher.highlight(text),
                    partial(app.open_more_info, eid),
                    help=state_text(hass.states.get(eid), eid),
                )


class CardCommands(Provider):
    """Jump to a card of the current view."""

    async def discover(self) -> Hits:
        app: HATuiApp = self.app  # type: ignore[assignment]
        for index, title in app.card_titles():
            yield DiscoveryHit(f"Go to {title}", partial(app.go_to_card, index), help="Dashboard card")

    async def search(self, query: str) -> Hits:
        app: HATuiApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for index, title in app.card_titles():
            text = f"Go to {title}"
            if (score := matcher.match(text)) > 0:
                yield Hit(score, matcher.highlight(text), partial(app.go_to_card, index), help="Dashboard card")


class ServiceCommands(Provider):
    """Open the service console prefilled with a service."""

    async def search(self, query: str) -> Hits:
        app: HATuiApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for name in app.hass.service_names():
            text = f"Call {name}"
            if (score := matcher.match(text)) > 0:
                yield Hit(score * 0.9, matcher.highlight(text), partial(app.open_console, name), help="Service")
