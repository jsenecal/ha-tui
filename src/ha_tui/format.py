"""State presentation shared by the CLI and the TUI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.text import Text

DOMAIN_ICONS = {
    "air_quality": "💨",
    "alarm_control_panel": "🚨",
    "automation": "🤖",
    "binary_sensor": "🔵",
    "button": "🔘",
    "calendar": "📅",
    "camera": "📷",
    "climate": "🔥",
    "counter": "🔢",
    "cover": "🪟",
    "device_tracker": "📍",
    "event": "⚡",
    "fan": "🌀",
    "group": "📦",
    "humidifier": "💧",
    "image": "🎨",
    "input_boolean": "🔘",
    "input_button": "🔘",
    "input_datetime": "📅",
    "input_number": "🔢",
    "input_select": "📋",
    "input_text": "📝",
    "lawn_mower": "🌱",
    "light": "💡",
    "lock": "🔒",
    "media_player": "🎵",
    "number": "🔢",
    "person": "👤",
    "plant": "🌿",
    "remote": "📡",
    "scene": "🎬",
    "schedule": "📅",
    "script": "📜",
    "select": "📋",
    "sensor": "📈",
    "siren": "🚨",
    "sun": "🌞",
    "switch": "🔌",
    "text": "📝",
    "timer": "⏳",
    "todo": "✅",
    "update": "🆙",
    "vacuum": "🧹",
    "valve": "🚰",
    "water_heater": "🛁",
    "weather": "⛅",
    "zone": "📌",
}
DEVICE_CLASS_ICONS = {
    "humidity": "💧",
    "battery": "🔋",
    "power": "⚡",
    "energy": "⚡",
    "illuminance": "🔆",
    "door": "🚪",
    "garage_door": "🚪",
    "window": "🪟",
    "motion": "🏃",
    "occupancy": "🏃",
    "presence": "🏠",
    "smoke": "🔥",
    "moisture": "💧",
    "connectivity": "🔗",
    "timestamp": "🕑",
    "lock": "🔒",
    "plug": "🔌",
}

# binary_sensor device_class -> (on label, off label), as the frontend translates them.
BINARY_LABELS = {
    "battery": ("Low", "Normal"),
    "battery_charging": ("Charging", "Not charging"),
    "cold": ("Cold", "Normal"),
    "connectivity": ("Connected", "Disconnected"),
    "door": ("Open", "Closed"),
    "garage_door": ("Open", "Closed"),
    "gas": ("Detected", "Clear"),
    "heat": ("Hot", "Normal"),
    "light": ("Light detected", "No light"),
    "lock": ("Unlocked", "Locked"),
    "moisture": ("Wet", "Dry"),
    "motion": ("Detected", "Clear"),
    "moving": ("Moving", "Not moving"),
    "occupancy": ("Detected", "Clear"),
    "opening": ("Open", "Closed"),
    "plug": ("Plugged in", "Unplugged"),
    "power": ("Detected", "Clear"),
    "presence": ("Home", "Away"),
    "problem": ("Problem", "OK"),
    "running": ("Running", "Not running"),
    "safety": ("Unsafe", "Safe"),
    "smoke": ("Detected", "Clear"),
    "sound": ("Detected", "Clear"),
    "tamper": ("Tampering detected", "Clear"),
    "update": ("Update available", "Up-to-date"),
    "vibration": ("Detected", "Clear"),
    "window": ("Open", "Closed"),
}

ACTIVE_STATES = {
    "on",
    "open",
    "opening",
    "closing",
    "playing",
    "home",
    "unlocked",
    "unlocking",
    "heat",
    "cool",
    "heat_cool",
    "auto",
    "dry",
    "fan_only",
    "cleaning",
    "active",
    "armed_home",
    "armed_away",
    "armed_night",
    "armed_vacation",
    "armed_custom_bypass",
    "triggered",
    "detected",
}
UNAVAILABLE = {"unavailable", "unknown", None}


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


# entity_id -> display precision from the entity registry (set by Hass.refresh).
DISPLAY_PRECISION: dict[str, int] = {}

STYLE_ACTIVE = "bold yellow"
STYLE_INACTIVE = "grey62"
STYLE_UNAVAILABLE = "red dim"
STYLE_VALUE = "bold cyan"


def icon(state: dict[str, Any] | None, entity_id: str) -> str:
    domain = domain_of(entity_id)
    if domain in ("sensor", "binary_sensor", "cover", "switch") and state:
        dc = state.get("attributes", {}).get("device_class")
        if dc in DEVICE_CLASS_ICONS:
            return DEVICE_CLASS_ICONS[dc]
    return DOMAIN_ICONS.get(domain, "•")


def humanize_time(value: str | None, now: datetime | None = None) -> str:
    if not value:
        return ""
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return value
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = ((now or datetime.now(UTC)) - when).total_seconds()
    future = seconds < 0
    seconds = abs(seconds)
    for limit, div, unit in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h"), (86400 * 60, 86400, "d")):
        if seconds < limit:
            amount = f"{int(seconds // div)}{unit}"
            break
    else:
        amount = when.astimezone().strftime("%Y-%m-%d")
        return amount
    return f"in {amount}" if future else f"{amount} ago"


def _num(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def state_text(state: dict[str, Any] | None, entity_id: str) -> str:
    """Human-readable state, roughly what the frontend shows on an entity row."""
    if state is None:
        return "not found"
    value = state.get("state")
    attrs = state.get("attributes", {})
    domain = domain_of(entity_id)
    if value in UNAVAILABLE:
        if value == "unknown" and domain in ("scene", "button", "input_button", "event"):
            return "—"
        return str(value or "unknown")

    if domain == "binary_sensor":
        on, off = BINARY_LABELS.get(attrs.get("device_class"), ("On", "Off"))
        return on if value == "on" else off
    if attrs.get("device_class") == "timestamp" or domain in ("scene", "button", "input_button", "event"):
        return humanize_time(value) or "—"
    if domain == "light" and value == "on" and attrs.get("brightness") is not None:
        return f"On · {round(attrs['brightness'] / 2.55)}%"
    if domain == "climate":
        parts = [str(value).replace("_", " ").capitalize()]
        unit = attrs.get("temperature_unit") or ""
        if attrs.get("current_temperature") is not None:
            parts.append(f"{_num(attrs['current_temperature'])}{unit}")
        if attrs.get("temperature") is not None:
            parts.append(f"→ {_num(attrs['temperature'])}{unit}")
        elif attrs.get("target_temp_low") is not None:
            parts.append(f"→ {_num(attrs['target_temp_low'])}–{_num(attrs['target_temp_high'])}{unit}")
        return " ".join(parts)
    if domain == "cover" and attrs.get("current_position") is not None and value == "open":
        return f"Open · {attrs['current_position']}%"
    if domain == "media_player" and value in ("playing", "paused"):
        title = attrs.get("media_title")
        artist = attrs.get("media_artist")
        if title:
            return f"{value.capitalize()} · {artist + ' – ' if artist else ''}{title}"
    if domain == "fan" and value == "on" and attrs.get("percentage") is not None:
        return f"On · {attrs['percentage']}%"
    if domain == "weather":
        temp = attrs.get("temperature")
        unit = attrs.get("temperature_unit", "")
        cond = str(value).replace("partlycloudy", "partly cloudy").replace("-", " ")
        return f"{cond.capitalize()} · {_num(temp)}{unit}" if temp is not None else cond.capitalize()
    if domain in ("person", "device_tracker"):
        return {"home": "Home", "not_home": "Away"}.get(value, str(value))

    unit = attrs.get("unit_of_measurement")
    text = str(value)
    if domain in ("sensor", "number", "input_number", "counter"):
        try:
            number = float(text)
            precision = DISPLAY_PRECISION.get(entity_id)
            text = f"{number:.{precision}f}" if isinstance(precision, int) else f"{number:g}"
        except ValueError:
            pass
    elif text in ("on", "off", "open", "closed", "locked", "unlocked", "idle", "playing", "paused", "home"):
        text = text.capitalize()
    else:
        text = text.replace("_", " ")
        text = text[:1].upper() + text[1:]
    return f"{text} {unit}" if unit else text


def state_style(state: dict[str, Any] | None) -> str:
    if state is None or state.get("state") in UNAVAILABLE:
        return STYLE_UNAVAILABLE
    value = state.get("state")
    if value in ACTIVE_STATES:
        return STYLE_ACTIVE
    if value in ("off", "closed", "locked", "idle", "standby", "not_home", "paused", "docked", "disarmed"):
        return STYLE_INACTIVE
    return STYLE_VALUE


def styled_state(state: dict[str, Any] | None, entity_id: str) -> Text:
    return Text(state_text(state, entity_id), style=state_style(state))
