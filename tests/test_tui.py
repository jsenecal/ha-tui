from __future__ import annotations

import asyncio

from textual.widgets import ContentSwitcher, DataTable, Input, Select

from ha_tui.config import Settings
from ha_tui.tui.app import HATuiApp
from ha_tui.tui.console import ServiceConsole
from ha_tui.tui.more_info import MoreInfo
from ha_tui.tui.panes import ActivityPane
from ha_tui.tui.widgets import CardWidget, EntityList, MarkdownBlock

from .mock_ha import TOKEN, MockHA


async def wait_for(pilot, predicate, limit: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def make_app(mock: MockHA, dashboard: str | None = "default") -> HATuiApp:
    return HATuiApp(Settings(url=mock.url, token=TOKEN), dashboard=dashboard)


def has_card(app: HATuiApp, title: str) -> bool:
    return any(c.card.title == title and c.query(EntityList) for c in app.query(CardWidget))


def row_states(app: HATuiApp, title: str) -> list[str]:
    card = next(c for c in app.query(CardWidget) if c.card.title == title)
    lst = card.query_one(EntityList)
    out = []
    for option in lst.options:
        grid = option.prompt
        out.append(str(grid.columns[2]._cells[0]))
    return out


async def test_overview_renders_and_toggles(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded and len(app.query(CardWidget)) == 5 and has_card(app, "Kitchen"))
        titles = [c.card.title for c in app.query(CardWidget)]
        assert "Living Room" in titles and "Kitchen" in titles
        assert row_states(app, "Kitchen")[0] == "On · 50%"

        kitchen = next(c for c in app.query(CardWidget) if c.card.title == "Kitchen").query_one(EntityList)
        kitchen.focus()
        kitchen.highlighted = 0
        await pilot.press("space")
        await wait_for(pilot, lambda: mock_ha.states["light.kitchen"]["state"] == "off")
        await wait_for(pilot, lambda: row_states(app, "Kitchen")[0] == "Off")
        assert mock_ha.calls[-1]["service"] == "toggle"


async def test_external_change_updates_row_and_activity(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded and has_card(app, "Kitchen"))
        await mock_ha.set_state("switch.coffee", "on")
        await wait_for(pilot, lambda: row_states(app, "Kitchen")[1] == "On")
        assert any("Coffee" in line.plain for line in app.query_one(ActivityPane).entries)
        await pilot.press("3")
        await wait_for(pilot, lambda: any("Coffee" in line.text for line in app.query_one("#activity #log").lines))


async def test_more_info_controls(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        app.open_more_info("climate.thermostat")
        await wait_for(pilot, lambda: isinstance(app.screen, MoreInfo) and app.screen.query("#choice-0"))
        await pilot.press("plus")
        await wait_for(pilot, lambda: mock_ha.states["climate.thermostat"]["attributes"]["temperature"] == 21.5)
        select = app.screen.query_one("#choice-0", Select)
        select.value = "off"
        await wait_for(pilot, lambda: mock_ha.states["climate.thermostat"]["state"] == "off")
        # The dialog follows the live state without re-firing the select.
        await wait_for(pilot, lambda: app.screen.query_one("#state").content.plain.startswith("Off"))
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, MoreInfo))
        app.open_more_info("sensor.kitchen_temp")
        await wait_for(pilot, lambda: isinstance(app.screen, MoreInfo) and len(app.screen.query("Sparkline")) == 1)


async def test_enter_opens_more_info_and_light_brightness(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded and has_card(app, "Kitchen"))
        kitchen = next(c for c in app.query(CardWidget) if c.card.title == "Kitchen").query_one(EntityList)
        kitchen.focus()
        kitchen.highlighted = 0
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(app.screen, MoreInfo) and app.screen.query("#actions"))
        assert app.screen.entity_id == "light.kitchen"
        await pilot.press("minus")
        await wait_for(pilot, lambda: mock_ha.states["light.kitchen"]["attributes"]["brightness"] == 102)


async def test_entities_pane_filter(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        await pilot.press("2")
        assert app.query_one("#main", ContentSwitcher).current == "entities"
        table = app.query_one("#entities DataTable", DataTable)
        assert table.row_count == len(mock_ha.states)
        app.query_one("#filter", Input).value = "domain:light"
        await wait_for(pilot, lambda: table.row_count == 2)
        app.query_one("#filter", Input).value = "area:kitchen state:off"
        await wait_for(pilot, lambda: table.row_count == 1)


async def test_service_console(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        app.open_console("light.turn_on", "light.living_room")
        await wait_for(pilot, lambda: isinstance(app.screen, ServiceConsole) and app.screen.query("#data"))
        app.screen.query_one("#data").text = "brightness_pct: 40"
        await pilot.press("ctrl+s")
        await wait_for(pilot, lambda: mock_ha.states["light.living_room"]["state"] == "on")
        assert mock_ha.calls[-1]["service_data"] == {"brightness_pct": 40}


async def test_reconnects(mock_ha: MockHA):
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        await mock_ha.drop_connections()
        await wait_for(pilot, lambda: len(mock_ha.connections) == 0)
        await wait_for(pilot, lambda: app.client.connected and len(mock_ha.connections) == 1, limit=8)
        await mock_ha.set_state("switch.coffee", "on")
        await wait_for(pilot, lambda: row_states(app, "Kitchen")[1] == "On")


async def test_custom_dashboard_markdown_template(mock_ha: MockHA):
    app = make_app(mock_ha, dashboard="custom")
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded and app.query(MarkdownBlock))
        block = app.query_one(MarkdownBlock)
        await wait_for(pilot, lambda: block.unsubscribe is not None)
        assert "Hello 12" in block.content.markup  # type: ignore[union-attr]


async def test_more_info_with_unset_select(mock_ha: MockHA):
    """Regression: a select with no current value (light off, effect=None) crashed the dialog."""
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        app.open_more_info("light.living_room")
        await wait_for(pilot, lambda: isinstance(app.screen, MoreInfo) and app.screen.query("#choice-0"))
        select = app.screen.query_one("#choice-0", Select)
        assert select.value is Select.NULL
        select.value = "Candle"
        await wait_for(pilot, lambda: mock_ha.calls and mock_ha.calls[-1]["service_data"] == {"effect": "Candle"})


async def test_closing_dialog_while_history_loads(mock_ha: MockHA):
    """Regression: the history worker mounted into a dialog that had already been closed."""
    mock_ha.history_delay = 0.3
    app = make_app(mock_ha)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        app.open_more_info("sensor.kitchen_temp")
        await wait_for(pilot, lambda: isinstance(app.screen, MoreInfo))
        await pilot.press("escape")
        await pilot.pause(0.6)
        assert app._exception is None


async def test_rapid_view_switching(mock_ha: MockHA):
    """Regression: a replaced view kept laying out its cards and mounted into a removed container."""
    app = make_app(mock_ha, dashboard="home")
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for(pilot, lambda: app._loaded)
        # Pause just long enough for each view's first layout to start before replacing it.
        for delay in (0, 0.001, 0.005, 0.01, 0.02):
            for view in app.dashboard.views:
                await app.show_view(view)
                await pilot.pause(delay)
        await pilot.pause(0.5)
        assert app._exception is None
        assert app.query(CardWidget)
