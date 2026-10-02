"""Per-domain remote-control actions (what the more-info dialog offers)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .format import domain_of

State = dict[str, Any]

# Service fired by the "activate" key, mirroring a tile card's default tap/icon action.
DEFAULT_ACTIONS: dict[str, tuple[str, str]] = {
    "automation": ("automation", "trigger"),
    "button": ("button", "press"),
    "climate": ("climate", "toggle"),
    "cover": ("cover", "toggle"),
    "fan": ("fan", "toggle"),
    "humidifier": ("humidifier", "toggle"),
    "input_boolean": ("input_boolean", "toggle"),
    "input_button": ("input_button", "press"),
    "light": ("light", "toggle"),
    "lock": ("lock", "toggle"),
    "media_player": ("media_player", "media_play_pause"),
    "remote": ("remote", "toggle"),
    "scene": ("scene", "turn_on"),
    "script": ("script", "turn_on"),
    "siren": ("siren", "toggle"),
    "switch": ("switch", "toggle"),
    "vacuum": ("vacuum", "start"),
    "valve": ("valve", "toggle"),
    "water_heater": ("water_heater", "toggle"),
    "group": ("homeassistant", "toggle"),
}


def default_action(entity_id: str, state: State | None = None) -> tuple[str, str] | None:
    domain = domain_of(entity_id)
    if domain == "lock" and state is not None:
        return ("lock", "unlock" if state.get("state") == "locked" else "lock")
    if domain == "vacuum" and state is not None and state.get("state") == "cleaning":
        return ("vacuum", "return_to_base")
    return DEFAULT_ACTIONS.get(domain)


@dataclass
class Action:
    key: str
    label: str
    service: str  # "domain.service"
    data: dict[str, Any] | Callable[[State], dict[str, Any] | None] = field(default_factory=dict)

    def resolve(self, state: State) -> dict[str, Any] | None:
        return self.data(state) if callable(self.data) else dict(self.data)


@dataclass
class Choice:
    label: str
    service: str
    field: str
    options: list[str]
    current: str | None


@dataclass
class Value:
    """A numeric/text field the user can set directly."""

    label: str
    service: str
    field: str
    current: Any
    numeric: bool = True
    min: float | None = None
    max: float | None = None


def _clamp(value: float, lo: float | None, hi: float | None) -> float:
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _round_step(value: float, step: float) -> float:
    return round(round(value / step) * step, 4)


def _attrs(state: State) -> dict[str, Any]:
    return state.get("attributes", {}) or {}


def _step_attr(
    attr: str, step_attr: str | None, default_step: float, sign: int, field: str, lo_attr: str, hi_attr: str
):
    def compute(state: State) -> dict[str, Any] | None:
        a = _attrs(state)
        current = a.get(attr)
        if current is None:
            return None
        step = float(a.get(step_attr) or default_step) if step_attr else default_step
        value = _round_step(float(current) + sign * step, step)
        return {field: _clamp(value, a.get(lo_attr), a.get(hi_attr))}

    return compute


def _color_temp(sign: int):
    def compute(state: State) -> dict[str, Any] | None:
        a = _attrs(state)
        current = a.get("color_temp_kelvin")
        if current is None:
            return None
        value = _clamp(current + sign * 250, a.get("min_color_temp_kelvin"), a.get("max_color_temp_kelvin"))
        return {"color_temp_kelvin": int(value)}

    return compute


def _volume(sign: int):
    def compute(state: State) -> dict[str, Any] | None:
        current = _attrs(state).get("volume_level")
        if current is None:
            return None
        return {"volume_level": round(_clamp(float(current) + sign * 0.05, 0, 1), 2)}

    return compute


def _position(sign: int):
    def compute(state: State) -> dict[str, Any] | None:
        current = _attrs(state).get("current_position")
        if current is None:
            return None
        return {"position": int(_clamp(current + sign * 10, 0, 100))}

    return compute


def _fan_pct(sign: int):
    def compute(state: State) -> dict[str, Any] | None:
        a = _attrs(state)
        step = float(a.get("percentage_step") or 10)
        current = float(a.get("percentage") or 0)
        return {"percentage": int(_clamp(_round_step(current + sign * step, step), 0, 100))}

    return compute


def _climate_temp(sign: int):
    def compute(state: State) -> dict[str, Any] | None:
        a = _attrs(state)
        step = float(a.get("target_temp_step") or (0.5 if (a.get("temperature_unit") or "°C") == "°C" else 1))
        if a.get("temperature") is not None:
            value = _clamp(
                _round_step(float(a["temperature"]) + sign * step, step), a.get("min_temp"), a.get("max_temp")
            )
            return {"temperature": value}
        if a.get("target_temp_low") is not None and a.get("target_temp_high") is not None:
            return {
                "target_temp_low": _round_step(float(a["target_temp_low"]) + sign * step, step),
                "target_temp_high": _round_step(float(a["target_temp_high"]) + sign * step, step),
            }
        return None

    return compute


def _supports(state: State, bit: int) -> bool:
    return bool(int(_attrs(state).get("supported_features") or 0) & bit)


def actions_for(entity_id: str, state: State) -> list[Action]:
    domain = domain_of(entity_id)
    a = _attrs(state)
    acts: list[Action] = []
    toggle = DEFAULT_ACTIONS.get(domain)
    if toggle and toggle[1] == "toggle":
        acts.append(Action("t", "Toggle", f"{toggle[0]}.toggle"))

    if domain == "light":
        acts += [Action("1", "On", "light.turn_on"), Action("0", "Off", "light.turn_off")]
        if a.get("supported_color_modes") and set(a["supported_color_modes"]) - {"onoff"}:
            acts += [
                Action("minus", "Dim −10%", "light.turn_on", {"brightness_step_pct": -10}),
                Action("plus", "Brighten +10%", "light.turn_on", {"brightness_step_pct": 10}),
            ]
        if "color_temp" in (a.get("supported_color_modes") or []):
            acts += [
                Action("left_square_bracket", "Warmer", "light.turn_on", _color_temp(-1)),
                Action("right_square_bracket", "Cooler", "light.turn_on", _color_temp(+1)),
            ]
    elif domain in ("switch", "input_boolean", "siren", "remote", "humidifier", "group"):
        svc = "homeassistant" if domain == "group" else domain
        acts += [Action("1", "On", f"{svc}.turn_on"), Action("0", "Off", f"{svc}.turn_off")]
        if domain == "humidifier":
            acts += [
                Action(
                    "minus",
                    "Humidity −1",
                    "humidifier.set_humidity",
                    _step_attr("humidity", None, 1, -1, "humidity", "min_humidity", "max_humidity"),
                ),
                Action(
                    "plus",
                    "Humidity +1",
                    "humidifier.set_humidity",
                    _step_attr("humidity", None, 1, +1, "humidity", "min_humidity", "max_humidity"),
                ),
            ]
    elif domain == "climate":
        acts += [
            Action("minus", "Target −", "climate.set_temperature", _climate_temp(-1)),
            Action("plus", "Target +", "climate.set_temperature", _climate_temp(+1)),
            Action("0", "Off", "climate.turn_off"),
            Action("1", "On", "climate.turn_on"),
        ]
    elif domain == "water_heater":
        acts += [
            Action(
                "minus",
                "Target −",
                "water_heater.set_temperature",
                _step_attr("temperature", None, 1, -1, "temperature", "min_temp", "max_temp"),
            ),
            Action(
                "plus",
                "Target +",
                "water_heater.set_temperature",
                _step_attr("temperature", None, 1, +1, "temperature", "min_temp", "max_temp"),
            ),
        ]
    elif domain == "cover":
        acts += [
            Action("o", "Open", "cover.open_cover"),
            Action("s", "Stop", "cover.stop_cover"),
            Action("c", "Close", "cover.close_cover"),
        ]
        if a.get("current_position") is not None:
            acts += [
                Action("minus", "Position −10", "cover.set_cover_position", _position(-1)),
                Action("plus", "Position +10", "cover.set_cover_position", _position(+1)),
            ]
    elif domain == "valve":
        acts += [Action("o", "Open", "valve.open_valve"), Action("c", "Close", "valve.close_valve")]
    elif domain == "fan":
        acts += [
            Action("1", "On", "fan.turn_on"),
            Action("0", "Off", "fan.turn_off"),
            Action("minus", "Speed −", "fan.set_percentage", _fan_pct(-1)),
            Action("plus", "Speed +", "fan.set_percentage", _fan_pct(+1)),
        ]
        if a.get("oscillating") is not None:
            acts.append(
                Action("o", "Oscillate", "fan.oscillate", lambda s: {"oscillating": not _attrs(s).get("oscillating")})
            )
    elif domain == "lock":
        acts += [Action("l", "Lock", "lock.lock"), Action("u", "Unlock", "lock.unlock")]
        if _supports(state, 1):
            acts.append(Action("o", "Open", "lock.open"))
    elif domain == "media_player":
        acts += [
            Action("space", "Play/Pause", "media_player.media_play_pause"),
            Action("p", "Previous", "media_player.media_previous_track"),
            Action("n", "Next", "media_player.media_next_track"),
            Action("minus", "Volume −", "media_player.volume_set", _volume(-1)),
            Action("plus", "Volume +", "media_player.volume_set", _volume(+1)),
            Action(
                "m",
                "Mute",
                "media_player.volume_mute",
                lambda s: {"is_volume_muted": not _attrs(s).get("is_volume_muted")},
            ),
            Action("1", "On", "media_player.turn_on"),
            Action("0", "Off", "media_player.turn_off"),
        ]
    elif domain == "vacuum":
        acts += [
            Action("s", "Start", "vacuum.start"),
            Action("p", "Pause", "vacuum.pause"),
            Action("h", "Return home", "vacuum.return_to_base"),
            Action("l", "Locate", "vacuum.locate"),
        ]
    elif domain == "lawn_mower":
        acts += [
            Action("s", "Start", "lawn_mower.start_mowing"),
            Action("p", "Pause", "lawn_mower.pause"),
            Action("h", "Dock", "lawn_mower.dock"),
        ]
    elif domain in ("number", "input_number"):
        down = _step_attr("_value", "step", 1, -1, "value", "min", "max")
        up = _step_attr("_value", "step", 1, +1, "value", "min", "max")
        acts += [
            Action("minus", "Decrease", f"{domain}.set_value", lambda s: down(numeric_state(s))),
            Action("plus", "Increase", f"{domain}.set_value", lambda s: up(numeric_state(s))),
        ]
    elif domain == "counter":
        acts += [
            Action("minus", "Decrement", "counter.decrement"),
            Action("plus", "Increment", "counter.increment"),
            Action("r", "Reset", "counter.reset"),
        ]
    elif domain == "timer":
        acts += [
            Action("s", "Start", "timer.start"),
            Action("p", "Pause", "timer.pause"),
            Action("c", "Cancel", "timer.cancel"),
        ]
    elif domain in ("scene",):
        acts.append(Action("enter", "Activate", "scene.turn_on"))
    elif domain == "script":
        acts += [Action("enter", "Run", "script.turn_on"), Action("0", "Stop", "script.turn_off")]
    elif domain == "automation":
        acts += [
            Action("enter", "Trigger", "automation.trigger"),
            Action("1", "Enable", "automation.turn_on"),
            Action("0", "Disable", "automation.turn_off"),
        ]
    elif domain in ("button", "input_button"):
        acts.append(Action("enter", "Press", f"{domain}.press"))
    elif domain == "update" and state.get("state") == "on":
        acts.append(Action("i", "Install", "update.install"))
    elif domain == "alarm_control_panel":
        acts += [
            Action("d", "Disarm", "alarm_control_panel.alarm_disarm"),
            Action("h", "Arm home", "alarm_control_panel.alarm_arm_home"),
            Action("a", "Arm away", "alarm_control_panel.alarm_arm_away"),
            Action("n", "Arm night", "alarm_control_panel.alarm_arm_night"),
        ]
    elif domain in ("sensor", "binary_sensor") or domain not in DEFAULT_ACTIONS:
        acts.append(Action("u", "Update", "homeassistant.update_entity"))
    return acts


def numeric_state(state: State) -> State:
    """number/input_number keep their value in `state`; expose it as an attribute for _step_attr."""
    try:
        value = float(state.get("state"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return state
    return {**state, "attributes": {**_attrs(state), "_value": value}}


def choices_for(entity_id: str, state: State) -> list[Choice]:
    domain = domain_of(entity_id)
    a = _attrs(state)
    out: list[Choice] = []

    def add(label: str, service: str, fld: str, options_attr: str, current: Any) -> None:
        options = a.get(options_attr)
        if isinstance(options, list) and options:
            out.append(
                Choice(label, service, fld, [str(o) for o in options], None if current is None else str(current))
            )

    if domain in ("select", "input_select"):
        add("Option", f"{domain}.select_option", "option", "options", state.get("state"))
    elif domain == "climate":
        add("Mode", "climate.set_hvac_mode", "hvac_mode", "hvac_modes", state.get("state"))
        add("Preset", "climate.set_preset_mode", "preset_mode", "preset_modes", a.get("preset_mode"))
        add("Fan", "climate.set_fan_mode", "fan_mode", "fan_modes", a.get("fan_mode"))
        add("Swing", "climate.set_swing_mode", "swing_mode", "swing_modes", a.get("swing_mode"))
    elif domain == "light":
        add("Effect", "light.turn_on", "effect", "effect_list", a.get("effect"))
    elif domain == "fan":
        add("Preset", "fan.set_preset_mode", "preset_mode", "preset_modes", a.get("preset_mode"))
    elif domain == "media_player":
        add("Source", "media_player.select_source", "source", "source_list", a.get("source"))
        add("Sound mode", "media_player.select_sound_mode", "sound_mode", "sound_mode_list", a.get("sound_mode"))
    elif domain == "humidifier":
        add("Mode", "humidifier.set_mode", "mode", "available_modes", a.get("mode"))
    elif domain == "water_heater":
        add("Mode", "water_heater.set_operation_mode", "operation_mode", "operation_list", a.get("operation_mode"))
    return out


def values_for(entity_id: str, state: State) -> list[Value]:
    domain = domain_of(entity_id)
    a = _attrs(state)
    if domain in ("number", "input_number"):
        return [Value("Value", f"{domain}.set_value", "value", state.get("state"), True, a.get("min"), a.get("max"))]
    if domain in ("text", "input_text"):
        return [Value("Text", f"{domain}.set_value", "value", state.get("state"), numeric=False)]
    if domain == "light" and "brightness" in a:
        pct = round(a["brightness"] / 2.55) if a.get("brightness") is not None else None
        return [Value("Brightness %", "light.turn_on", "brightness_pct", pct, True, 0, 100)]
    if domain == "climate" and "temperature" in a:
        return [
            Value(
                "Target",
                "climate.set_temperature",
                "temperature",
                a.get("temperature"),
                True,
                a.get("min_temp"),
                a.get("max_temp"),
            )
        ]
    if domain == "cover" and a.get("current_position") is not None:
        return [Value("Position %", "cover.set_cover_position", "position", a.get("current_position"), True, 0, 100)]
    if domain == "media_player" and a.get("volume_level") is not None:
        return [
            Value("Volume %", "media_player.volume_set", "_volume_pct", round(a["volume_level"] * 100), True, 0, 100)
        ]
    if domain == "alarm_control_panel" and a.get("code_format"):
        return [Value("Code", "", "code", "", numeric=False)]
    return []
