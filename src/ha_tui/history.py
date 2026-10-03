"""Entity history: fetching, numeric statistics and state timelines (shared by the TUI and CLI)."""

from __future__ import annotations

import bisect
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rich.text import Text

from .client import HAClient, HACommandError
from .format import ACTIVE_STATES, STYLE_UNAVAILABLE

# (label, seconds) — the TUI binds keys 1..6 to these.
RANGES: list[tuple[str, int]] = [
    ("1h", 3600),
    ("6h", 6 * 3600),
    ("24h", 24 * 3600),
    ("3d", 3 * 86400),
    ("7d", 7 * 86400),
    ("30d", 30 * 86400),
]
# Beyond this, raw history is probably purged (recorder keeps 10 days by default):
# use long-term statistics when the entity has them.
STATISTICS_AFTER = 7 * 86400
UNAVAILABLE = {"unavailable", "unknown", ""}


def to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class Series:
    entity_id: str
    start: float
    end: float
    # (epoch seconds, raw state) for history; for statistics the state is the hourly mean.
    points: list[tuple[float, str]] = field(default_factory=list)
    numeric: bool = False
    source: str = "history"  # or "statistics"
    # statistics only: (epoch, min, max) per period
    bands: list[tuple[float, float, float]] = field(default_factory=list)

    def append(self, when: float, state: str) -> None:
        """Live update: add a state change and slide the window forward."""
        if self.points and self.points[-1][1] == state:
            return
        self.points.append((when, state))
        self.end = max(self.end, when)

    def numeric_points(self) -> list[tuple[float, float | None]]:
        return [(t, to_float(s)) for t, s in self.points]


def _looks_numeric(points: list[tuple[float, str]], current: str | None) -> bool:
    values = [s for _, s in points if s not in UNAVAILABLE]
    if current is not None and current not in UNAVAILABLE:
        values.append(current)
    return bool(values) and all(to_float(v) is not None for v in values)


async def fetch(client: HAClient, entity_id: str, seconds: int, state: dict[str, Any] | None = None) -> Series:
    end = time.time()
    start = end - seconds
    current = (state or {}).get("state")
    has_statistics = bool((state or {}).get("attributes", {}).get("state_class"))
    if seconds > STATISTICS_AFTER and has_statistics:
        series = await _fetch_statistics(client, entity_id, start, end)
        if series.points:
            return series
    rows = await client.send(
        "history/history_during_period",
        start_time=datetime.fromtimestamp(start, UTC).isoformat(),
        end_time=datetime.fromtimestamp(end, UTC).isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        no_attributes=True,
        significant_changes_only=False,
    )
    points = [
        (max(start, float(r.get("lu") or r.get("lc") or start)), str(r.get("s", ""))) for r in rows.get(entity_id, [])
    ]
    return Series(entity_id, start, end, points, numeric=_looks_numeric(points, current))


async def _fetch_statistics(client: HAClient, entity_id: str, start: float, end: float) -> Series:
    try:
        result = await client.send(
            "recorder/statistics_during_period",
            start_time=datetime.fromtimestamp(start, UTC).isoformat(),
            end_time=datetime.fromtimestamp(end, UTC).isoformat(),
            statistic_ids=[entity_id],
            period="hour",
            types=["mean", "min", "max"],
        )
    except HACommandError:
        return Series(entity_id, start, end)
    series = Series(entity_id, start, end, numeric=True, source="statistics")
    for row in result.get(entity_id, []):
        when = row["start"] / 1000 if row["start"] > 1e11 else row["start"]
        if (mean := to_float(row.get("mean"))) is None:
            continue
        series.points.append((when, str(mean)))
        lo, hi = to_float(row.get("min")), to_float(row.get("max"))
        series.bands.append((when, mean if lo is None else lo, mean if hi is None else hi))
    return series


# ---------------------------------------------------------------------- #
@dataclass
class NumericStats:
    minimum: float
    maximum: float
    average: float  # time-weighted over the window
    last: float | None
    changes: int


def numeric_stats(series: Series) -> NumericStats | None:
    points = [(t, v) for t, v in series.numeric_points() if v is not None]
    if not points:
        return None
    lows = [lo for _, lo, _ in series.bands] or [v for _, v in points]
    highs = [hi for _, _, hi in series.bands] or [v for _, v in points]
    # Time-weighted mean: each value holds until the next change (or the end of the window).
    weighted = 0.0
    total = 0.0
    timeline = series.numeric_points()
    for (t, v), (t_next, _) in zip(timeline, [*timeline[1:], (series.end, None)], strict=True):
        if v is None:
            continue
        span = max(0.0, t_next - t)
        weighted += v * span
        total += span
    average = weighted / total if total else sum(v for _, v in points) / len(points)
    last = to_float(series.points[-1][1]) if series.points else None
    return NumericStats(min(lows), max(highs), average, last, len(series.points))


@dataclass
class Segment:
    state: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def segments(series: Series) -> list[Segment]:
    out: list[Segment] = []
    for (t, s), (t_next, _) in zip(series.points, [*series.points[1:], (series.end, "")], strict=True):
        if out and out[-1].state == s:
            out[-1].end = t_next
        else:
            out.append(Segment(s, t, t_next))
    return out


def state_totals(series: Series) -> list[tuple[str, float]]:
    """Total time spent in each state, longest first."""
    totals: dict[str, float] = {}
    for seg in segments(series):
        totals[seg.state] = totals.get(seg.state, 0.0) + seg.duration
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def state_at(series: Series, when: float) -> str | None:
    current = None
    for t, s in series.points:
        if t > when:
            break
        current = s
    return current


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def time_ticks(start: float, end: float, count: int) -> list[tuple[float, str]]:
    """Evenly spaced tick positions with labels suited to the span."""
    span = end - start
    fmt = "%H:%M" if span <= 86400 else ("%a %H:%M" if span <= 7 * 86400 else "%b %d")
    count = max(2, count)
    return [(t, time.strftime(fmt, time.localtime(t))) for t in (start + span * i / (count - 1) for i in range(count))]


INACTIVE = {"off", "closed", "locked", "idle", "standby", "not_home", "paused", "docked", "disarmed", "clear"}
PALETTE = ["cyan", "magenta", "green", "blue", "bright_magenta", "bright_cyan", "bright_green", "orange3"]


def state_color(state: str, others: dict[str, str]) -> str:
    """Stable colour per state: active yellow, inactive grey, others from a palette (recorded in `others`)."""
    if state in UNAVAILABLE:
        return STYLE_UNAVAILABLE
    if state in ACTIVE_STATES:
        return "yellow"
    if state in INACTIVE:
        return "grey50"
    if state not in others:
        others[state] = PALETTE[len(others) % len(PALETTE)]
    return others[state]


def timeline_text(series: Series, width: int, colors: dict[str, str]) -> Text:
    """Two rows of coloured cells over a row of time labels.

    Each column covers a slice of the window. A slice shows the series' usual (longest) state unless something
    else happened in it, so a 3-second motion event in a 24h window still gets a visible column.
    """
    span = series.end - series.start
    totals = state_totals(series)
    usual = totals[0][0] if totals else None
    times = [t for t, _ in series.points]
    bar = Text()
    for col in range(width):
        t0 = series.start + span * col / width
        t1 = series.start + span * (col + 1) / width
        present = {state_at(series, t0)} | {
            s for t, s in series.points[bisect.bisect_right(times, t0) : bisect.bisect_right(times, t1)]
        }
        present.discard(None)
        unusual = [s for s in present if s != usual]
        state = unusual[0] if unusual else usual if present else None
        bar.append("█", style=state_color(state, colors) if state is not None else "grey15")
    ticks = Text(" " * width, style="dim")
    for t, label in time_ticks(series.start, series.end, max(2, width // 14)):
        pos = min(width - len(label), max(0, round((t - series.start) / span * (width - 1)) - len(label) // 2))
        ticks = ticks[:pos] + Text(label, style="dim") + ticks[pos + len(label) :]
    return Text("\n").join([bar, bar, ticks])
