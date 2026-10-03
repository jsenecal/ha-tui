"""Full-screen entity history: a chart for numeric sensors, a timeline for everything else."""

from __future__ import annotations

import itertools
import time
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static
from textual_plotext import PlotextPlot

from ..format import STYLE_ACTIVE, icon, state_text
from ..history import (
    RANGES,
    Series,
    fetch,
    format_duration,
    numeric_stats,
    segments,
    state_color,
    state_totals,
    time_ticks,
    timeline_text,
)

if TYPE_CHECKING:
    from .app import HATuiApp

DEFAULT_RANGE = 2  # 24h


class Timeline(Static):
    """One coloured cell per column: the state the entity was in at that moment."""

    DEFAULT_CSS = "Timeline { height: 3; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.series: Series | None = None
        self.state_colors: dict[str, str] = {}

    def show(self, series: Series, colors: dict[str, str]) -> None:
        self.series, self.state_colors = series, colors
        self.refresh()

    def render(self) -> Text:
        if self.series is None or not self.series.points:
            return Text("no history", style="dim")
        return timeline_text(self.series, max(1, self.size.width), self.state_colors)


class HistoryScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HistoryScreen { background: $background 80%; }
    HistoryScreen > #history {
        width: 100%; height: 100%;
        border: thick $accent 50%;
        background: $surface;
        padding: 0 1;
    }
    HistoryScreen #title { text-style: bold; height: 1; }
    HistoryScreen #ranges { height: 1; margin: 1 0 0 0; }
    HistoryScreen #ranges Button { min-width: 7; margin-right: 1; }
    HistoryScreen #ranges Button.active { background: $accent; color: $text; text-style: bold; }
    HistoryScreen #summary { height: auto; margin: 1 0; }
    HistoryScreen #body { height: 1fr; }
    HistoryScreen PlotextPlot { height: 1fr; }
    HistoryScreen DataTable { height: 1fr; margin-top: 1; }
    HistoryScreen #hint { height: 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        Binding("r", "reload", "Refresh"),
    ]

    def __init__(self, entity_id: str, range_index: int = DEFAULT_RANGE) -> None:
        super().__init__()
        self.entity_id = entity_id
        self.range_index = range_index
        self.series: Series | None = None
        self.state_colors: dict[str, str] = {}

    @property
    def ha(self) -> HATuiApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        hass = self.ha.hass
        st = hass.states.get(self.entity_id)
        with Vertical(id="history"):
            yield Static(
                f"{icon(st, self.entity_id)}  {hass.name(self.entity_id)}   [dim]{self.entity_id}[/dim]", id="title"
            )
            with Horizontal(id="ranges"):
                for i, (label, _) in enumerate(RANGES):
                    button = Button(f"{label} ({i + 1})", id=f"range-{i}", compact=True)
                    # Keys 1-6 switch ranges; a focused button would look selected.
                    button.can_focus = False
                    yield button
            yield Static("Loading…", id="summary")
            yield Vertical(id="body")
            yield Static("1–6 time range · r refresh · esc close", id="hint")

    def on_mount(self) -> None:
        self.load()

    # ------------------------------------------------------------------ #
    @work(exclusive=True, group="history")
    async def load(self) -> None:
        for i in range(len(RANGES)):
            self.query_one(f"#range-{i}", Button).set_class(i == self.range_index, "active")
        self.query_one("#summary", Static).update(Text("Loading…", style="dim"))
        seconds = RANGES[self.range_index][1]
        try:
            series = await fetch(self.ha.client, self.entity_id, seconds, self.ha.hass.states.get(self.entity_id))
        except Exception as exc:
            if self.is_attached:
                self.query_one("#summary", Static).update(Text(f"Could not load history: {exc}", style="red"))
            return
        # The screen may have been closed while history was loading.
        if not self.is_attached:
            return
        self.series = series
        self.state_colors = {}
        body = self.query_one("#body", Vertical)
        await body.remove_children()
        if series.numeric:
            await body.mount(PlotextPlot(id="plot"))
        else:
            table: DataTable = DataTable(id="changes", cursor_type="row", zebra_stripes=True)
            table.add_column("When", key="when", width=19)
            table.add_column("State", key="state", width=30)
            table.add_column("For", key="for", width=12)
            await body.mount(Timeline(id="timeline"), table)
        self.redraw()

    def redraw(self) -> None:
        if self.series is None or not self.is_attached:
            return
        if self.series.numeric:
            self._draw_chart(self.series)
        else:
            self._draw_timeline(self.series)

    def _unit(self) -> str:
        return (self.ha.hass.states.get(self.entity_id) or {}).get("attributes", {}).get("unit_of_measurement") or ""

    def _draw_chart(self, series: Series) -> None:
        unit = self._unit()
        stats = numeric_stats(series)
        label = RANGES[self.range_index][0]
        summary = Text()
        if stats is None:
            summary.append("No numeric values in this range.", style="dim")
        else:
            for name, value in (("min", stats.minimum), ("avg", stats.average), ("max", stats.maximum)):
                summary.append(f"{name} ", style="dim")
                summary.append(f"{value:.2f}{unit}   ", style="bold cyan")
            if (current := self.ha.hass.states.get(self.entity_id)) is not None:
                summary.append("now ", style="dim")
                summary.append(state_text(current, self.entity_id), style=STYLE_ACTIVE)
            source = "hourly mean, min–max in gray" if series.source == "statistics" else f"{stats.changes} changes"
            summary.append(f"   · last {label} · {source}", style="dim")
        self.query_one("#summary", Static).update(summary)

        plot = next(iter(self.query("#plot").results(PlotextPlot)), None)
        if plot is None:
            return
        if not plot.size.width:
            # Not laid out yet; the tick count depends on the width.
            self.call_after_refresh(self.redraw)
            return
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        # Draw each run of numeric values separately so unavailable periods show as gaps;
        # a value holds until the next change, so the last one extends to now.
        if series.bands:
            # min/max first so the mean line stays on top
            xs = [t for t, _, _ in series.bands]
            plt.plot(xs, [lo for _, lo, _ in series.bands], marker="braille", color="gray")
            plt.plot(xs, [hi for _, _, hi in series.bands], marker="braille", color="gray")
        timeline = [*series.numeric_points(), (series.end, None)]
        run_x: list[float] = []
        run_y: list[float] = []
        for (t, v), (t_next, _) in itertools.pairwise(timeline):
            if v is None:
                if run_x:
                    plt.plot(run_x, run_y, marker="braille", color="cyan")
                run_x, run_y = [], []
                continue
            run_x.append(t)
            run_y.append(v)
            if t_next == series.end:
                run_x.append(t_next)
                run_y.append(v)
        if run_x:
            plt.plot(run_x, run_y, marker="braille", color="cyan")
        ticks = time_ticks(series.start, series.end, max(2, plot.size.width // 14))
        plt.xticks([t for t, _ in ticks], [label for _, label in ticks])
        plt.xlim(series.start, series.end)
        plt.ylabel(unit)
        plot.refresh()

    def _draw_timeline(self, series: Series) -> None:
        span = series.end - series.start
        summary = Text()
        for state, seconds in state_totals(series)[:6]:
            summary.append("■ ", style=state_color(state, self.state_colors))
            summary.append(
                state_text({**(self.ha.hass.states.get(self.entity_id) or {}), "state": state}, self.entity_id)
            )
            summary.append(f" {format_duration(seconds)} ({seconds / span:.0%})   ", style="dim")
        if not series.points:
            summary = Text("No state changes recorded in this range.", style="dim")
        self.query_one("#summary", Static).update(summary)
        if (timeline := next(iter(self.query("#timeline").results(Timeline)), None)) is not None:
            timeline.show(series, self.state_colors)
        table = next(iter(self.query("#changes").results(DataTable)), None)
        if table is None:
            return
        table.clear()
        base = self.ha.hass.states.get(self.entity_id) or {}
        for seg in reversed(segments(series)):
            when = time.strftime("%a %b %d %H:%M:%S", time.localtime(seg.start))
            shown = Text(
                state_text({**base, "state": seg.state}, self.entity_id),
                style=state_color(seg.state, self.state_colors),
            )
            table.add_row(when, shown, format_duration(seg.duration))

    # ------------------------------------------------------------------ #
    def entity_updated(self) -> None:
        """Live update from the app's state flush."""
        st = self.ha.hass.states.get(self.entity_id)
        if self.series is None or st is None or self.series.source == "statistics":
            return
        self.series.end = time.time()
        self.series.append(self.series.end, str(st.get("state")))
        self.redraw()

    def select_range(self, index: int) -> None:
        if index != self.range_index or self.series is None:
            self.range_index = index
            self.load()

    @on(Button.Pressed)
    def _range_button(self, event: Button.Pressed) -> None:
        if (event.button.id or "").startswith("range-"):
            self.select_range(int(event.button.id.removeprefix("range-")))

    def on_key(self, event: events.Key) -> None:
        if event.key.isdigit() and 1 <= int(event.key) <= len(RANGES):
            event.stop()
            event.prevent_default()
            self.select_range(int(event.key) - 1)

    def on_resize(self) -> None:
        self.redraw()

    def action_reload(self) -> None:
        self.load()

    def action_dismiss(self) -> None:
        self.dismiss(None)
