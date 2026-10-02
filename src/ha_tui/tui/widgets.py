"""Dashboard widgets: entity rows, cards and a masonry view."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..format import icon, state_style, state_text
from ..lovelace.model import Card, Item, View

if TYPE_CHECKING:
    from ..hass import Hass

CARD_MIN_WIDTH = 42


def row_prompt(hass: Hass, item: Item) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=2, no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", no_wrap=True, overflow="ellipsis", max_width=28)
    if item.kind == "area" and item.area_id:
        area = hass.areas.get(item.area_id, {})
        grid.add_row("🏠", item.name or area.get("name", item.area_id), Text("›", style="dim"))
        return grid
    eid = item.entity_id or ""
    st = hass.states.get(eid)
    grid.add_row(icon(st, eid), item.name or hass.name(eid), Text(state_text(st, eid), style=state_style(st)))
    return grid


class EntityList(OptionList):
    """The rows of one card. Up/down past either end moves to the neighbouring card."""

    DEFAULT_CSS = """
    EntityList {
        height: auto;
        max-height: 9999;
        border: none;
        padding: 0;
        background: transparent;
    }
    EntityList:focus { border: none; }
    EntityList > .option-list--option { padding: 0 1; }
    EntityList:blur > .option-list--option-highlighted { background: transparent; text-style: none; }
    """

    class Activated(Message):
        def __init__(self, item: Item, quick: bool) -> None:
            super().__init__()
            self.item = item
            self.quick = quick

    def __init__(self, hass: Hass, items: list[Item], key_prefix: str) -> None:
        self.hass = hass
        self.items = items
        self.key_prefix = key_prefix
        super().__init__(*[Option(row_prompt(hass, it), id=f"{key_prefix}-{i}") for i, it in enumerate(items)])

    def option_id_for(self, index: int) -> str:
        return f"{self.key_prefix}-{index}"

    def refresh_item(self, index: int) -> None:
        self.replace_option_prompt(self.option_id_for(index), row_prompt(self.hass, self.items[index]))

    def current_item(self) -> Item | None:
        if self.highlighted is None:
            return None
        return self.items[self.highlighted]

    def on_focus(self) -> None:
        if self.highlighted is None and self.option_count:
            self.highlighted = 0

    def on_blur(self) -> None:
        self.highlighted = None

    def action_cursor_down(self) -> None:
        if self.highlighted is not None and self.highlighted >= self.option_count - 1:
            self.screen.focus_next(EntityList)
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.highlighted is not None and self.highlighted == 0:
            previous = self.screen.focus_previous(EntityList)
            if isinstance(previous, EntityList) and previous.option_count:
                previous.highlighted = previous.option_count - 1
            return
        super().action_cursor_up()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.Activated(self.items[event.option_index], quick=False))

    async def on_key(self, event: events.Key) -> None:
        if event.key in ("space", "t") and (item := self.current_item()) is not None:
            event.stop()
            self.post_message(self.Activated(item, quick=True))


class MarkdownBlock(Static):
    DEFAULT_CSS = "MarkdownBlock { padding: 0 1; height: auto; }"

    def __init__(self, text: str) -> None:
        super().__init__(Markdown(text))
        self.source = text
        self.unsubscribe: Callable[[], Any] | None = None

    @property
    def is_template(self) -> bool:
        return "{{" in self.source or "{%" in self.source

    def set_rendered(self, text: str) -> None:
        self.update(Markdown(text))

    def on_mount(self) -> None:
        subscribe = getattr(self.app, "subscribe_markdown", None)
        if self.is_template and subscribe is not None:
            self.run_worker(subscribe(self))

    async def on_unmount(self) -> None:
        if self.unsubscribe is not None:
            await self.unsubscribe()


class CardWidget(Vertical):
    DEFAULT_CSS = """
    CardWidget {
        height: auto;
        border: round $panel-lighten-2;
        border-title-color: $text-accent;
        border-title-style: bold;
        margin: 0 0 1 0;
        padding: 0;
    }
    CardWidget:focus-within { border: round $accent; }
    """

    def __init__(self, card: Card, hass: Hass, index: int) -> None:
        super().__init__(id=f"card-{index}")
        self.card = card
        self.hass = hass
        self.index = index
        self.border_title = card.title or None

    def compose(self) -> ComposeResult:
        # Consecutive selectable rows share one list; markdown blocks sit between them.
        run: list[Item] = []
        part = 0
        for item in self.card.items:
            if item.kind == "markdown":
                if run:
                    yield EntityList(self.hass, run, f"c{self.index}p{part}")
                    run, part = [], part + 1
                yield MarkdownBlock(item.text or "")
            else:
                run.append(item)
        if run:
            yield EntityList(self.hass, run, f"c{self.index}p{part}")

    def estimated_height(self) -> int:
        return 2 + sum(1 if i.kind != "markdown" else max(1, (i.text or "").count("\n") + 1) for i in self.card.items)


class ViewWidget(VerticalScroll):
    """A Lovelace view: badges on top, cards distributed in masonry columns like the frontend."""

    DEFAULT_CSS = """
    ViewWidget { padding: 0 1; }
    ViewWidget > #badges { height: auto; margin: 0 0 1 0; }
    ViewWidget > #badges > Static { width: auto; margin-right: 2; }
    ViewWidget > #columns { height: auto; }
    ViewWidget > #columns > Vertical { height: auto; width: 1fr; margin-right: 1; }
    ViewWidget > #notice { color: $warning; margin-bottom: 1; }
    """

    def __init__(self, view: View, hass: Hass, notice: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.view = view
        self.hass = hass
        self.notice = notice
        self.columns = 0

    def compose(self) -> ComposeResult:
        if self.notice or self.view.error:
            yield Static(self.view.error or self.notice or "", id="notice")
        if self.view.badges:
            with Horizontal(id="badges"):
                for badge in self.view.badges:
                    yield Static(self._badge_text(badge), classes="badge")
        yield Horizontal(id="columns")
        if not self.view.cards:
            yield Static("[dim]This view has no entities to show.[/dim]")

    def _badge_text(self, badge: Item) -> Text:
        eid = badge.entity_id or ""
        st = self.hass.states.get(eid)
        return Text.assemble(
            f"{icon(st, eid)} ",
            (badge.name or self.hass.name(eid), "bold"),
            " ",
            (state_text(st, eid), state_style(st)),
        )

    def on_resize(self, event: events.Resize) -> None:
        columns = max(1, event.size.width // CARD_MIN_WIDTH)
        if columns != self.columns:
            self.columns = columns
            self.call_after_refresh(self.layout_cards)

    async def layout_cards(self) -> None:
        container = self.query_one("#columns", Horizontal)
        focused = self.app.focused
        refocus = None
        if isinstance(focused, EntityList) and focused in container.query(EntityList):
            refocus = (focused.key_prefix, focused.highlighted)
        await container.remove_children()
        cols = [Vertical() for _ in range(self.columns)]
        heights = [0] * self.columns
        placement: list[list[CardWidget]] = [[] for _ in range(self.columns)]
        for i, card in enumerate(self.view.cards):
            widget = CardWidget(card, self.hass, i)
            col = heights.index(min(heights))
            placement[col].append(widget)
            heights[col] += widget.estimated_height()
        await container.mount_all(cols)
        for col, widgets in zip(cols, placement, strict=True):
            await col.mount_all(widgets)
        self.post_message(self.Laidout())
        if refocus:
            for lst in self.query(EntityList):
                if lst.key_prefix == refocus[0]:
                    lst.focus()
                    lst.highlighted = refocus[1]

    class Laidout(Message):
        pass

    def refresh_badges(self, entity_id: str) -> None:
        for widget, badge in zip(self.query(".badge"), self.view.badges, strict=False):
            if badge.entity_id == entity_id and isinstance(widget, Static):
                widget.update(self._badge_text(badge))

    def card_widget(self, index: int) -> CardWidget | None:
        try:
            return self.query_one(f"#card-{index}", CardWidget)
        except Exception:
            return None
