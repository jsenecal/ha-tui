"""The Textual application."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Footer, Header, LoadingIndicator, OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option

from ..client import HAClient, HAError
from ..config import Settings
from ..controls import default_action
from ..hass import Hass
from ..lovelace.model import Dashboard, View
from ..lovelace.resolve import load_dashboard
from .commands import CardCommands, EntityCommands, ServiceCommands
from .console import ServiceConsole
from .more_info import MoreInfo
from .panes import ActivityPane, EntitiesPane
from .widgets import EntityList, MarkdownBlock, ViewWidget

log = logging.getLogger(__name__)

RECONNECT_DELAYS = [1, 2, 5, 10, 30]
PANES = {"dashboard": "Dashboard", "entities": "All entities", "activity": "Activity"}


class HATuiApp(App):
    TITLE = "Home Assistant"
    CSS = """
    #body { height: 1fr; }
    #sidebar {
        width: 28; height: 1fr;
        border: none; border-right: vkey $panel-lighten-2;
        padding: 0;
    }
    #sidebar.hidden { display: none; }
    #main { width: 1fr; height: 1fr; }
    #dashboard { height: 1fr; }
    #view-tabs { display: none; }
    #view-tabs.visible { display: block; }
    #view-host { height: 1fr; }
    #status { padding: 1 2; color: $text-muted; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("1", "show_pane('dashboard')", "Dashboard"),
        Binding("2", "show_pane('entities')", "Entities"),
        Binding("3", "show_pane('activity')", "Activity"),
        Binding("slash", "command_palette", "Search"),
        Binding("colon", "open_console", "Service"),
        Binding("r", "reload", "Reload"),
        Binding("b", "toggle_sidebar", "Sidebar"),
        Binding("f", "filter_entities", "Filter", show=False),
        Binding("escape,backspace", "back", "Back", show=False),
    ]
    COMMANDS = App.COMMANDS | {EntityCommands, CardCommands, ServiceCommands}

    def __init__(self, settings: Settings, dashboard: str | None = None, view: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.dashboard_path = dashboard
        self.initial_view = view
        self.client = HAClient(settings.url, settings.token, settings.verify_ssl)
        self.hass = Hass()
        self.dashboard: Dashboard | None = None
        self.current_view: View | None = None
        self.history: list[str] = []
        self._rows: dict[str, list[tuple[EntityList, int]]] = {}
        self._pending: set[str] = set()
        self._flush_scheduled = False
        self._unsubscribe: Any = None
        self._loaded = False
        self.client.on_disconnect(self._on_disconnect)

    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield OptionList(id="sidebar")
            with ContentSwitcher(id="main", initial="loading"):
                with Vertical(id="loading"):
                    yield Static("Connecting…", id="status")
                    yield LoadingIndicator()
                with Vertical(id="dashboard"):
                    yield Tabs(id="view-tabs")
                    yield Vertical(id="view-host")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = urlparse(self.settings.url).netloc
        self.connect()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen) and action not in ("quit", "command_palette"):
            return False
        return self._loaded or action == "quit"

    # ------------------------------------------------------------------ #
    # connection
    # ------------------------------------------------------------------ #
    def _set_status(self, text: str) -> None:
        host = urlparse(self.settings.url).netloc
        self.sub_title = f"{host} · {text}"
        if not self._loaded:
            self.query_one("#status", Static).update(text)

    @work(exclusive=True, group="connection")
    async def connect(self) -> None:
        attempt = 0
        while True:
            try:
                self._set_status("connecting…")
                await self.client.connect()
                self._set_status("loading registries…")
                await self.hass.refresh(self.client)
                self._unsubscribe = await self.client.subscribe_states(self._on_state_changed)
                break
            except HAError as exc:
                await self.client.close()
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                self._set_status(f"✗ {exc} — retrying in {delay}s")
                log.warning("connect failed: %s", exc)
                attempt += 1
                await asyncio.sleep(delay)

        if not self._loaded:
            await self._first_load()
        else:
            self.refresh_all_rows()
            self.notify("Reconnected to Home Assistant", timeout=3)
        self._set_status(f"HA {self.client.ha_version} · {len(self.hass.states)} entities")

    def _on_disconnect(self) -> None:
        self._set_status("connection lost — reconnecting…")
        self.notify("Lost connection to Home Assistant", severity="warning", timeout=4)
        self.connect()

    async def _first_load(self) -> None:
        self._set_status("building dashboard…")
        await self.load_dashboard()
        main = self.query_one("#main", ContentSwitcher)
        await main.add_content(EntitiesPane(self.hass, id="entities"))
        await main.add_content(ActivityPane(self.hass, id="activity"))
        main.current = "dashboard"
        self._loaded = True
        self.refresh_sidebar()
        self.refresh_bindings()

    async def load_dashboard(self) -> None:
        try:
            self.dashboard = await load_dashboard(self.client, self.hass, self.dashboard_path)
        except HAError as exc:
            self.notify(f"Could not load dashboard: {exc}", severity="error")
            self.dashboard = Dashboard(url_path=self.dashboard_path, title="Overview")
        self.title = self.dashboard.title
        visible = [v for v in self.dashboard.views if not v.subview]
        tabs = self.query_one("#view-tabs", Tabs)
        await tabs.clear()
        for view in visible:
            await tabs.add_tab(Tab(view.title, id=f"view-{view.path}"))
        tabs.set_class(len(visible) > 1, "visible")
        target = (self.initial_view and self.dashboard.view_by_path(self.initial_view)) or (
            visible or self.dashboard.views or [None]
        )[0]
        self.history.clear()
        await self.show_view(target)

    async def show_view(self, view: View | None, push: bool = False) -> None:
        if push and self.current_view is not None:
            self.history.append(self.current_view.path)
        self.current_view = view
        host = self.query_one("#view-host", Vertical)
        await host.remove_children()
        self._rows.clear()
        if view is None:
            await host.mount(Static("This dashboard has no views.", id="status"))
        else:
            notice = self.dashboard.notice if self.dashboard else None
            await host.mount(ViewWidget(view, self.hass, notice=notice, id="view"))
            if not view.subview:
                tabs = self.query_one("#view-tabs", Tabs)
                with tabs.prevent(Tabs.TabActivated):
                    tabs.active = f"view-{view.path}"
        self.refresh_sidebar()

    @on(ViewWidget.Laidout)
    def _index_rows(self) -> None:
        self._rows.clear()
        for lst in self.query(EntityList):
            for index, item in enumerate(lst.items):
                if item.entity_id:
                    self._rows.setdefault(item.entity_id, []).append((lst, index))
        if self.focused is None or self.focused is self.query_one("#sidebar"):
            first = next(iter(self.query("#view EntityList")), None)
            if first is not None and self.query_one("#main", ContentSwitcher).current == "dashboard":
                first.focus()

    @on(Tabs.TabActivated, "#view-tabs")
    async def _tab(self, event: Tabs.TabActivated) -> None:
        if self.dashboard and event.tab.id:
            await self.show_view(self.dashboard.view_by_path(event.tab.id.removeprefix("view-")))

    # ------------------------------------------------------------------ #
    # live updates
    # ------------------------------------------------------------------ #
    def _on_state_changed(self, new: dict[str, Any], old: dict[str, Any] | None) -> None:
        self.hass.apply_state(new)
        if self._loaded:
            self.query_one(ActivityPane).record(new, old)
        self._pending.add(new["entity_id"])
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.set_timer(0.1, self._flush)

    def _flush(self) -> None:
        self._flush_scheduled = False
        pending, self._pending = self._pending, set()
        view = self.query("#view").first(ViewWidget) if self.query("#view") else None
        entities = self.query(EntitiesPane).first() if self._loaded else None
        for eid in pending:
            for lst, index in self._rows.get(eid, []):
                if lst.is_attached:
                    lst.refresh_item(index)
            if view is not None and self.current_view and any(b.entity_id == eid for b in self.current_view.badges):
                view.refresh_badges(eid)
            if entities is not None:
                entities.entity_updated(eid)
        if isinstance(self.screen, MoreInfo) and self.screen.entity_id in pending:
            self.screen.entity_updated()

    def refresh_all_rows(self) -> None:
        self._pending.update(self.hass.states)
        self._pending.update(self._rows)
        self._flush()

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def call(self, domain: str, service: str, entity_id: str | None = None, data: dict[str, Any] | None = None) -> None:
        self._call(domain, service, entity_id, data or {})

    @work(group="calls")
    async def _call(self, domain: str, service: str, entity_id: str | None, data: dict[str, Any]) -> None:
        target = {"entity_id": entity_id} if entity_id else None
        try:
            await self.client.call_service(domain, service, data, target)
        except HAError as exc:
            self.notify(f"{domain}.{service} failed: {exc}", severity="error", timeout=6)
            return
        label = self.hass.name(entity_id) if entity_id else ""
        self.notify(f"{domain}.{service} {label}", timeout=1.5)

    @on(EntityList.Activated)
    async def _activated(self, event: EntityList.Activated) -> None:
        item = event.item
        if item.kind == "area":
            if self.dashboard and item.path and (view := self.dashboard.view_by_path(item.path)):
                await self.show_view(view, push=True)
            return
        if not item.entity_id:
            return
        if event.quick and (action := default_action(item.entity_id, self.hass.states.get(item.entity_id))):
            self.call(action[0], action[1], item.entity_id)
        else:
            self.open_more_info(item.entity_id)

    @on(EntitiesPane.Open)
    def _open_from_list(self, event: EntitiesPane.Open) -> None:
        self.open_more_info(event.entity_id)

    def open_more_info(self, entity_id: str) -> None:
        self.push_screen(MoreInfo(entity_id))

    def open_console(self, service: str = "", target: str = "") -> None:
        self.push_screen(ServiceConsole(service, target))

    def action_open_console(self) -> None:
        target = ""
        focused = self.focused
        if isinstance(focused, EntityList) and (item := focused.current_item()) and item.entity_id:
            target = item.entity_id
        self.open_console("", target)

    def action_show_pane(self, pane: str) -> None:
        self.query_one("#main", ContentSwitcher).current = pane
        if pane == "entities":
            self.query_one("#entities DataTable").focus()
        elif pane == "dashboard":
            first = next(iter(self.query("#view EntityList")), None)
            if first is not None:
                first.focus()
        self.refresh_sidebar()

    def action_filter_entities(self) -> None:
        self.action_show_pane("entities")
        self.query_one(EntitiesPane).focus_filter()

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("hidden")

    async def action_back(self) -> None:
        if self.history and self.dashboard:
            await self.show_view(self.dashboard.view_by_path(self.history.pop()))

    @work(exclusive=True, group="reload")
    async def action_reload(self) -> None:
        self.notify("Reloading…", timeout=1)
        await self.hass.refresh(self.client)
        await self.load_dashboard()
        self.query_one(EntitiesPane).populate()

    # ------------------------------------------------------------------ #
    # sidebar
    # ------------------------------------------------------------------ #
    def card_titles(self) -> list[tuple[int, str]]:
        if self.current_view is None:
            return []
        return [(i, c.title) for i, c in enumerate(self.current_view.cards) if c.title]

    def refresh_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", OptionList)
        current = self.query_one("#main", ContentSwitcher).current
        sidebar.clear_options()
        for key, label in PANES.items():
            marker = "▸ " if key == current else "  "
            sidebar.add_option(Option(f"{marker}{label}", id=f"pane:{key}"))
        if self.current_view is not None:
            sidebar.add_option(None)
            title = self.current_view.title + ("  ⟵ esc" if self.history else "")
            sidebar.add_option(Option(f"[b]{title}[/b]", id="view-title", disabled=True))
            for index, title in self.card_titles():
                sidebar.add_option(Option(f"  {title}", id=f"card:{index}"))

    @on(OptionList.OptionSelected, "#sidebar")
    def _sidebar_selected(self, event: OptionList.OptionSelected) -> None:
        kind, _, value = (event.option.id or "").partition(":")
        if kind == "pane":
            self.action_show_pane(value)
        elif kind == "card":
            self.go_to_card(int(value))

    def go_to_card(self, index: int) -> None:
        self.action_show_pane("dashboard")
        view = self.query("#view").first(ViewWidget)
        card = view.card_widget(index)
        if card is None:
            return
        view.scroll_to_widget(card, top=True)
        lst = next(iter(card.query(EntityList)), None)
        if lst is not None:
            lst.focus()

    # ------------------------------------------------------------------ #
    async def subscribe_markdown(self, block: MarkdownBlock) -> None:
        if not self.client.connected:
            return
        try:
            block.unsubscribe = await self.client.subscribe_template(block.source, block.set_rendered)
        except HAError as exc:
            block.set_rendered(f"*template error: {exc}*")

    async def on_unmount(self) -> None:
        await self.client.close()
