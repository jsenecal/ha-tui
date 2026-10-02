"""Non-dashboard panes: every entity (filterable) and a live activity log."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widgets import DataTable, Input, RichLog, Static, Switch

from ..format import domain_of, humanize_time, icon, state_style, state_text

if TYPE_CHECKING:
    from ..hass import Hass


class EntitiesPane(Vertical):
    DEFAULT_CSS = """
    EntitiesPane { padding: 0 1; }
    EntitiesPane > #filter-bar { height: auto; }
    EntitiesPane #filter { width: 1fr; }
    EntitiesPane #count { width: auto; padding: 1 1 0 2; color: $text-muted; }
    EntitiesPane DataTable { height: 1fr; }
    """

    class Open(Message):
        def __init__(self, entity_id: str) -> None:
            super().__init__()
            self.entity_id = entity_id

    def __init__(self, hass: Hass, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hass = hass
        self._debounce: Timer | None = None
        self._shown: set[str] = set()

    def compose(self) -> ComposeResult:
        with Horizontal(id="filter-bar"):
            yield Input(placeholder="Filter: name, entity_id, area, or domain:light area:kitchen state:on", id="filter")
            yield Static(id="count")
        table: DataTable = DataTable(id="entities", cursor_type="row", zebra_stripes=True)
        table.add_column("", key="icon", width=2)
        table.add_column("Name", key="name", width=36)
        table.add_column("Entity", key="entity", width=40)
        table.add_column("Area", key="area", width=18)
        table.add_column("State", key="state", width=26)
        table.add_column("Changed", key="changed", width=9)
        yield table

    def on_mount(self) -> None:
        self.populate()

    def _matches(self, eid: str, terms: list[str]) -> bool:
        st = self.hass.states.get(eid, {})
        name = self.hass.name(eid).casefold()
        area = (self.hass.area_name(eid) or "").casefold()
        for term in terms:
            if term.startswith("domain:"):
                if domain_of(eid) != term[7:]:
                    return False
            elif term.startswith("area:"):
                if term[5:] not in area and term[5:] != (self.hass.area_id(eid) or ""):
                    return False
            elif term.startswith("state:"):
                if str(st.get("state", "")).casefold() != term[6:]:
                    return False
            elif term not in eid and term not in name and term not in area:
                return False
        return True

    def populate(self) -> None:
        terms = self.query_one("#filter", Input).value.casefold().split()
        table = self.query_one("#entities", DataTable)
        cursor_key = None
        if table.row_count and table.cursor_row is not None:
            cursor_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        ids = sorted(
            (e for e in self.hass.states if self._matches(e, terms)), key=lambda e: self.hass.name(e).casefold()
        )
        for eid in ids:
            table.add_row(*self._cells(eid), key=eid)
        self._shown = set(ids)
        self.query_one("#count", Static).update(f"{len(ids)} / {len(self.hass.states)}")
        if cursor_key in self._shown:
            table.move_cursor(row=table.get_row_index(cursor_key))

    def _cells(self, eid: str) -> tuple[Any, ...]:
        st = self.hass.states.get(eid)
        return (
            icon(st, eid),
            self.hass.name(eid),
            Text(eid, style="dim"),
            self.hass.area_name(eid) or "",
            Text(state_text(st, eid), style=state_style(st)),
            Text(humanize_time((st or {}).get("last_changed")), style="dim"),
        )

    @on(Input.Changed, "#filter")
    def _filter_changed(self) -> None:
        if self._debounce:
            self._debounce.stop()
        self._debounce = self.set_timer(0.25, self.populate)

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self) -> None:
        self.query_one("#entities", DataTable).focus()

    @on(DataTable.RowSelected)
    def _selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value:
            self.post_message(self.Open(event.row_key.value))

    def entity_updated(self, eid: str) -> None:
        if eid not in self._shown:
            return
        table = self.query_one("#entities", DataTable)
        st = self.hass.states.get(eid)
        if st is None:
            table.remove_row(eid)
            self._shown.discard(eid)
            return
        table.update_cell(eid, "state", Text(state_text(st, eid), style=state_style(st)))
        table.update_cell(eid, "changed", Text(humanize_time(st.get("last_changed")), style="dim"))

    def focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()


class ActivityPane(Vertical):
    DEFAULT_CSS = """
    ActivityPane { padding: 0 1; }
    ActivityPane > #activity-bar { height: auto; }
    ActivityPane #activity-filter { width: 1fr; }
    ActivityPane #attr-label { width: auto; padding: 1 1 0 2; }
    ActivityPane RichLog { height: 1fr; }
    """

    def __init__(self, hass: Hass, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hass = hass
        self.entries: deque[Text] = deque(maxlen=2000)

    def compose(self) -> ComposeResult:
        with Horizontal(id="activity-bar"):
            yield Input(placeholder="Only log entities matching…", id="activity-filter")
            yield Static("attribute changes", id="attr-label")
            yield Switch(value=False, id="attributes")
        yield RichLog(id="log", max_lines=2000, wrap=False, markup=False, auto_scroll=True)

    def record(self, new: dict[str, Any], old: dict[str, Any] | None) -> None:
        eid = new["entity_id"]
        same = old is not None and old.get("state") == new.get("state")
        if same and not self.query_one("#attributes", Switch).value:
            return
        term = self.query_one("#activity-filter", Input).value.casefold().strip()
        if term and term not in eid and term not in self.hass.name(eid).casefold():
            return
        line = Text.assemble(
            (datetime.now().strftime("%H:%M:%S "), "dim"),
            f"{icon(new, eid)} ",
            (self.hass.name(eid), "bold"),
            (f"  {eid}  ", "dim"),
        )
        if old is not None:
            line.append(state_text(old, eid), style=state_style(old))
            line.append(" → ", style="dim")
        if new.get("state") is None:
            line.append("removed", style="red")
        else:
            line.append(state_text(new, eid), style=state_style(new))
        if same:
            line.append("  (attributes)", style="dim italic")
        self.entries.append(line)
        # A hidden RichLog queues writes without limit; replay from `entries` on show instead.
        if self.display:
            self.query_one("#log", RichLog).write(line)

    def on_show(self) -> None:
        log = self.query_one("#log", RichLog)
        log.clear()
        for line in self.entries:
            log.write(line)
