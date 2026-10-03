from __future__ import annotations

import asyncio

from textual.widgets import DataTable
from textual_plotext import PlotextPlot
from typer.testing import CliRunner

from ha_tui.cli import app as cli
from ha_tui.client import HAClient
from ha_tui.config import Settings
from ha_tui.hass import Hass
from ha_tui.history import (
    Series,
    fetch,
    format_duration,
    numeric_stats,
    segments,
    state_totals,
    timeline_text,
)
from ha_tui.tui.app import HATuiApp
from ha_tui.tui.history import HistoryScreen, Timeline
from ha_tui.tui.widgets import CardWidget, EntityList

from .mock_ha import TOKEN, MockHA, ThreadedMockHA


async def test_numeric_history(client: HAClient, hass: Hass):
    series = await fetch(client, "sensor.outside", 3600, hass.states["sensor.outside"])
    assert series.numeric and series.source == "history"
    assert [s for _, s in series.points] == ["20", "21", "unavailable", "21.5", "22", "12"]
    stats = numeric_stats(series)
    assert stats is not None
    assert (stats.minimum, stats.maximum, stats.last) == (12.0, 22.0, 12.0)
    # Time-weighted over five equal slices that have a value (the unavailable one is skipped).
    assert round(stats.average, 2) == round((20 + 21 + 21.5 + 22 + 12) / 5, 2)


async def test_long_ranges_use_statistics_when_available(client: HAClient, hass: Hass, mock_ha: MockHA):
    series = await fetch(client, "sensor.kitchen_temp", 30 * 86400, hass.states["sensor.kitchen_temp"])
    assert series.source == "statistics" and series.numeric
    assert len(series.points) == 48 and len(series.bands) == 48
    stats = numeric_stats(series)
    assert (stats.minimum, stats.maximum) == (19.0, 23.0)  # from the min/max bands, not the means
    # No state_class means no long-term statistics: fall back to raw history.
    series = await fetch(client, "sensor.outside", 30 * 86400, hass.states["sensor.outside"])
    assert series.source == "history"


async def test_state_history_timeline(client: HAClient, hass: Hass):
    series = await fetch(client, "binary_sensor.door", 86400, hass.states["binary_sensor.door"])
    assert not series.numeric
    assert [s.state for s in segments(series)] == ["off", "on", "off"]
    totals = dict(state_totals(series))
    assert round(totals["on"]) == 5  # the brief opening
    # A 5-second event in a 24h window still gets a visible column.
    bar = timeline_text(series, 80, {}).split("\n")[0]
    assert 1 <= [str(span.style) for span in bar.spans].count("yellow") <= 2


def test_series_live_append():
    series = Series("light.x", start=0, end=100, points=[(0, "off")])
    series.append(50, "on")
    series.append(60, "on")  # unchanged state: ignored
    assert series.points == [(0, "off"), (50, "on")]


def test_format_duration():
    assert [format_duration(s) for s in (5, 125, 7300, 90000)] == ["5s", "2m 05s", "2h 01m", "1d 1h"]


def test_history_cli():
    runner = CliRunner()
    with ThreadedMockHA() as mock:
        base = ["--url", mock.url, "--token", TOKEN, "history"]
        result = runner.invoke(cli, [*base, "sensor.outside", "--range", "6h"], env={"COLUMNS": "100"})
        assert result.exit_code == 0, result.output
        assert "min" in result.output and "max" in result.output and "last 6h" in result.output
        result = runner.invoke(cli, [*base, "Front Door"], env={"COLUMNS": "100"})
        assert result.exit_code == 0, result.output
        assert "Recent changes" in result.output and "Open" in result.output and "Closed" in result.output
        result = runner.invoke(cli, [*base, "sensor.outside", "--range", "2w"])
        assert result.exit_code != 0


async def _wait(pilot, predicate, limit: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await pilot.pause(0.05)


async def test_tui_history_screen(mock_ha: MockHA):
    app = HATuiApp(Settings(url=mock_ha.url, token=TOKEN), dashboard="default")
    async with app.run_test(size=(140, 45)) as pilot:
        await _wait(pilot, lambda: app._loaded and any(c.card.title == "Kitchen" for c in app.query(CardWidget)))
        kitchen = next(c for c in app.query(CardWidget) if c.card.title == "Kitchen").query_one(EntityList)
        kitchen.focus()
        kitchen.highlighted = [i.entity_id for i in kitchen.items].index("sensor.kitchen_temp")
        await pilot.press("h")
        await _wait(pilot, lambda: isinstance(app.screen, HistoryScreen) and app.screen.query(PlotextPlot))
        screen = app.screen
        assert screen.series.numeric and "min" in screen.query_one("#summary").content.plain

        await pilot.press("6")  # 30 days: hourly statistics
        await _wait(pilot, lambda: screen.series is not None and screen.series.source == "statistics")
        assert "hourly mean" in screen.query_one("#summary").content.plain
        await pilot.press("escape")
        await _wait(pilot, lambda: not isinstance(app.screen, HistoryScreen))

        app.open_history("binary_sensor.door")
        await _wait(pilot, lambda: isinstance(app.screen, HistoryScreen) and app.screen.query(Timeline))
        screen = app.screen
        table = screen.query_one("#changes", DataTable)
        await _wait(pilot, lambda: table.row_count == 3)
        # Live: a new state change shows up without reloading.
        await mock_ha.set_state("binary_sensor.door", "on")
        await _wait(pilot, lambda: table.row_count == 4)
        assert app._exception is None
