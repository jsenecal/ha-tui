from __future__ import annotations

import asyncio

from ha_tui.client import HAClient
from ha_tui.config import Settings
from ha_tui.hass import Hass
from ha_tui.lovelace.home import summary_text
from ha_tui.lovelace.resolve import default_dashboard, list_dashboards, load_dashboard
from ha_tui.tui.app import HATuiApp
from ha_tui.tui.widgets import CardWidget, EntityList

from .mock_ha import TOKEN, MockHA


def _cards(view):
    return [(c.title, [i.entity_id or i.area_id or i.key or i.kind for i in c.items]) for c in view.cards]


async def _home(client: HAClient, hass: Hass):
    return await load_dashboard(client, hass, "home")


async def test_home_is_default_when_panel_exists(client: HAClient, hass: Hass, mock_ha: MockHA):
    assert default_dashboard(hass) == "home"
    assert (await list_dashboards(client))[0] == ("home", "Home (new Overview)")
    mock_ha.panels.remove("home")
    await hass.refresh(client)
    assert default_dashboard(hass) is None


async def test_home_overview_layout(client: HAClient, hass: Hass):
    dash = await _home(client, hass)
    overview = dash.views[0]
    assert overview.path == "overview" and not overview.subview
    assert overview.header == "Welcome Alice"
    assert _cards(overview) == [
        ("Favorites", ["switch.coffee", "light.kitchen"]),  # predicted, unknown entity dropped
        ("Summaries", ["repairs", "light", "climate", "security", "media_players", "maintenance"]),
        ("Main Floor", ["living_room", "kitchen"]),
        ("Other areas", ["garage", "navigate"]),
    ]
    # Only non-subview views are tabs; areas, summaries and devices are subviews.
    assert [v.path for v in dash.views if not v.subview] == ["overview"]
    assert {"areas-kitchen", "light", "climate", "security", "maintenance", "media-players", "other-devices"} <= {
        v.path for v in dash.views
    }


async def test_home_favorites_come_first_and_hidden_suggestions(client: HAClient, hass: Hass, mock_ha: MockHA):
    mock_ha.home_config = {"favorite_entities": ["cover.blinds"], "hide_welcome_message": True}
    dash = await _home(client, hass)
    assert _cards(dash.views[0])[0] == ("Favorites", ["cover.blinds", "switch.coffee", "light.kitchen"])
    assert dash.views[0].header is None
    mock_ha.home_config = {
        "hide_suggested_entities": True,
        "shortcuts": [{"type": "summary", "key": "light", "hidden": True}],
    }
    dash = await _home(client, hass)
    titles = [c.title for c in dash.views[0].cards]
    assert "Favorites" not in titles
    summaries = next(c for c in dash.views[0].cards if c.title == "Summaries")
    assert "light" not in [i.key for i in summaries.items]


async def test_summary_texts(client: HAClient, hass: Hass):
    dash = await _home(client, hass)
    items = {i.key: i for i in dash.views[0].cards[1].items}
    text = {k: summary_text(hass, k, i.members)[0] for k, i in items.items()}
    assert text == {
        "repairs": "1 repair",
        "light": "1 light on",
        "climate": "21.5°",
        "security": "1 lock unlocked",
        "media_players": "1 playing",
        "maintenance": "1 low battery",
    }
    assert items["light"].path == "light"


async def test_home_area_view(client: HAClient, hass: Hass):
    kitchen = (await _home(client, hass)).view_by_path("areas-kitchen")
    assert kitchen is not None and kitchen.subview
    assert [b.entity_id for b in kitchen.badges] == ["sensor.kitchen_temp"]
    assert _cards(kitchen) == [
        ("Lights", ["kitchen", "light.kitchen"]),  # all-lights toggle row, then the light
        ("Others", ["switch.coffee", "sensor.kitchen_temp"]),  # diagnostic uptime sensor left out
    ]
    assert kitchen.cards[0].items[1].name == "Ceiling"  # area name stripped


async def test_summary_views(client: HAClient, hass: Hass):
    dash = await _home(client, hass)
    assert _cards(dash.view_by_path("light")) == [
        ("Main Floor › Living Room", ["living_room", "light.living_room"]),
        ("Main Floor › Kitchen", ["kitchen", "light.kitchen"]),
    ]
    assert _cards(dash.view_by_path("security")) == [
        ("Main Floor › Living Room", ["lock.front"]),
        ("Unassigned", ["binary_sensor.door"]),
    ]
    assert _cards(dash.view_by_path("maintenance")) == [("Low battery", ["sensor.door_battery"])]
    devices = dash.view_by_path("other-devices")
    assert _cards(devices) == [("Door Sensor", ["binary_sensor.door"])]
    assert devices.cards[0].items[0].name == "Contact"  # entity name without the device name


def _card(app: HATuiApp, title: str) -> EntityList | None:
    card = next((c for c in app.query(CardWidget) if c.card.title == title), None)
    return next(iter(card.query(EntityList)), None) if card else None


async def _wait(pilot, predicate, limit: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await pilot.pause(0.05)


def _detail(lst: EntityList, index: int) -> str:
    return str(lst.options[index].prompt.columns[2]._cells[0])


async def test_tui_home_navigation_and_area_lights(mock_ha: MockHA):
    app = HATuiApp(Settings(url=mock_ha.url, token=TOKEN))
    async with app.run_test(size=(140, 50)) as pilot:
        await _wait(pilot, lambda: app._loaded and _card(app, "Summaries") is not None)
        assert app.dashboard.url_path == "home"
        summaries = _card(app, "Summaries")
        light_index = [i.key for i in summaries.items].index("light")
        assert _detail(summaries, light_index).startswith("1 light on")

        # Live: the summary row follows its member entities.
        await mock_ha.set_state("light.living_room", "on")
        await _wait(pilot, lambda: _detail(_card(app, "Summaries"), light_index).startswith("2 lights on"))

        summaries = _card(app, "Summaries")
        summaries.focus()
        summaries.highlighted = light_index
        await pilot.press("enter")
        await _wait(pilot, lambda: app.current_view.path == "light" and _card(app, "Main Floor › Kitchen") is not None)

        kitchen = _card(app, "Main Floor › Kitchen")
        kitchen.focus()
        kitchen.highlighted = 0  # the all-lights row; a light is on, so it turns the area off
        await pilot.press("space")
        await _wait(pilot, lambda: mock_ha.states["light.kitchen"]["state"] == "off")
        assert mock_ha.calls[-1]["service"] == "turn_off"
        assert mock_ha.calls[-1]["target"] == {"area_id": "kitchen"}

        await pilot.press("escape")
        await _wait(pilot, lambda: app.current_view.path == "overview")
