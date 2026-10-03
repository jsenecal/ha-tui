"""Entity more-info dialog: live state, domain controls, history and attributes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Sparkline, Static

from ..controls import Action, Choice, Value, actions_for, choices_for, values_for
from ..format import humanize_time, icon, state_style, state_text

if TYPE_CHECKING:
    from .app import HATuiApp

KEY_LABELS = {
    "plus": "+",
    "minus": "−",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "space": "␣",
    "enter": "⏎",
}
# Extra keys that trigger the same action (e.g. "=" is unshifted "+").
KEY_ALIASES = {"equals_sign": "plus", "right": "plus", "left": "minus", "underscore": "minus"}


class MoreInfo(ModalScreen[None]):
    DEFAULT_CSS = """
    MoreInfo { align: center middle; }
    MoreInfo > #dialog {
        width: 84; max-width: 95%;
        height: auto; max-height: 90%;
        border: thick $accent 60%;
        background: $surface;
        padding: 1 2;
    }
    MoreInfo #title { text-style: bold; }
    MoreInfo #subtitle { color: $text-muted; }
    MoreInfo #state { margin: 1 0; text-style: bold; height: auto; }
    MoreInfo #actions { height: auto; layout: grid; grid-size: 3; grid-gutter: 0 1; grid-rows: 1; margin-bottom: 1; }
    MoreInfo #actions Button { width: 100%; min-width: 10; }
    MoreInfo .field { height: auto; margin-bottom: 1; }
    MoreInfo .field Label { width: 14; padding-top: 1; }
    MoreInfo .field Select, MoreInfo .field Input { width: 1fr; }
    MoreInfo #history { height: auto; margin-bottom: 1; }
    MoreInfo #history Sparkline { height: 3; }
    MoreInfo #attrs { height: auto; max-height: 14; border-top: solid $panel-lighten-2; padding-top: 1; }
    """
    BINDINGS = [Binding("escape,q", "dismiss", "Close")]

    def __init__(self, entity_id: str) -> None:
        super().__init__()
        self.entity_id = entity_id
        self.actions: dict[str, Action] = {}
        self.choices: list[Choice] = []
        self.values: list[Value] = []
        self.alarm_code = ""

    @property
    def ha(self) -> HATuiApp:
        return self.app  # type: ignore[return-value]

    @property
    def state(self) -> dict[str, Any]:
        return self.ha.hass.states.get(self.entity_id) or {"entity_id": self.entity_id, "state": None, "attributes": {}}

    def compose(self) -> ComposeResult:
        st = self.state
        hass = self.ha.hass
        acts = actions_for(self.entity_id, st)
        self.actions = {a.key: a for a in acts}
        self.choices = choices_for(self.entity_id, st)
        self.values = values_for(self.entity_id, st)
        with VerticalScroll(id="dialog"):
            yield Static(f"{icon(st, self.entity_id)}  {hass.name(self.entity_id)}", id="title", markup=False)
            sub = self.entity_id + (f"  ·  {area}" if (area := hass.area_name(self.entity_id)) else "")
            yield Static(sub, id="subtitle", markup=False)
            yield Static(id="state")
            if acts:
                with Vertical(id="actions"):
                    for a in acts:
                        yield Button(f"{a.label} ({KEY_LABELS.get(a.key, a.key)})", id=f"act-{a.key}", compact=True)
            for i, choice in enumerate(self.choices):
                with Horizontal(classes="field"):
                    yield Label(choice.label)
                    yield Select(
                        [(o, o) for o in choice.options],
                        value=choice.current if choice.current in choice.options else Select.NULL,
                        id=f"choice-{i}",
                        allow_blank=choice.current not in choice.options,
                    )
            for i, value in enumerate(self.values):
                with Horizontal(classes="field"):
                    yield Label(value.label)
                    yield Input(
                        "" if value.current is None else str(value.current),
                        id=f"value-{i}",
                        type="number" if value.numeric else "text",
                        password=value.field == "code",
                        placeholder="press Enter to apply",
                    )
            yield Vertical(id="history")
            yield Static(id="attrs")

    def on_mount(self) -> None:
        self.refresh_state()
        self.load_history()

    def refresh_state(self) -> None:
        st = self.state
        text = Text(state_text(st, self.entity_id), style=state_style(st))
        if changed := st.get("last_changed"):
            text.append(f"   changed {humanize_time(changed)}", style="dim not bold")
        self.query_one("#state", Static).update(text)

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold", no_wrap=True)
        grid.add_column(overflow="fold")
        for key, value in sorted((st.get("attributes") or {}).items()):
            if key in ("friendly_name", "icon", "entity_picture"):
                continue
            grid.add_row(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        grid.add_row(
            "last_updated", Text(f"{humanize_time(st.get('last_updated'))}  {st.get('last_updated', '')}", style="dim")
        )
        self.query_one("#attrs", Static).update(grid)

        fresh = choices_for(self.entity_id, st)
        for i, (old, new) in enumerate(zip(self.choices, fresh, strict=False)):
            if old.current != new.current and new.current in new.options:
                select = self.query_one(f"#choice-{i}", Select)
                with select.prevent(Select.Changed):
                    select.value = new.current
        self.choices = fresh or self.choices

        for i, value in enumerate(values_for(self.entity_id, st)):
            try:
                field = self.query_one(f"#value-{i}", Input)
            except Exception:
                continue
            if not field.has_focus and value.field != "code":
                with field.prevent(Input.Changed):
                    field.value = "" if value.current is None else str(value.current)

    @work(exclusive=True, group="history")
    async def load_history(self) -> None:
        st = self.state
        try:
            float(st.get("state"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        try:
            points = await self.ha.client.history(self.entity_id, hours=24)
        except Exception:
            return
        data = []
        for p in points:
            try:
                data.append(float(p.get("s", p.get("state"))))
            except (TypeError, ValueError):
                continue
        if len(data) < 2:
            return
        unit = st.get("attributes", {}).get("unit_of_measurement") or ""
        avg = sum(data) / len(data)
        box = self.query_one("#history", Vertical)
        await box.mount(
            Static(
                Text.assemble(
                    ("Last 24h  ", "bold"),
                    (f"min {min(data):g}{unit}  ·  max {max(data):g}{unit}  ·  avg {avg:.2f}{unit}", "dim"),
                )
            ),
            Sparkline(data[-240:], summary_function=max),
        )

    # ------------------------------------------------------------------ #
    def run_action(self, action: Action) -> None:
        data = action.resolve(self.state)
        if data is None:
            self.notify(f"{action.label}: not supported in the current state", severity="warning")
            return
        if action.service.startswith("alarm_control_panel.") and self.alarm_code:
            data["code"] = self.alarm_code
        domain, service = action.service.split(".", 1)
        self.ha.call(domain, service, self.entity_id, data)

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        key = (event.button.id or "").removeprefix("act-")
        if action := self.actions.get(key):
            self.run_action(action)

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input) and event.key not in ("escape",):
            return
        key = KEY_ALIASES.get(event.key, event.key)
        if isinstance(self.focused, (Button, Select)) and key in ("enter", "space"):
            return
        if action := self.actions.get(key):
            event.stop()
            event.prevent_default()
            self.run_action(action)

    @on(Select.Changed)
    def _choice(self, event: Select.Changed) -> None:
        index = int((event.select.id or "choice-0").removeprefix("choice-"))
        choice = self.choices[index]
        if event.value is Select.NULL or event.value == choice.current:
            return
        domain, service = choice.service.split(".", 1)
        self.ha.call(domain, service, self.entity_id, {choice.field: event.value})

    @on(Input.Submitted)
    def _value(self, event: Input.Submitted) -> None:
        index = int((event.input.id or "value-0").removeprefix("value-"))
        value = self.values[index]
        if value.field == "code":
            self.alarm_code = event.value
            self.notify("Code set for this dialog")
            return
        raw: Any = event.value
        if value.numeric:
            try:
                raw = float(event.value)
            except ValueError:
                self.notify("Not a number", severity="error")
                return
            if value.min is not None:
                raw = max(float(value.min), raw)
            if value.max is not None:
                raw = min(float(value.max), raw)
            raw = int(raw) if raw.is_integer() else raw
        field = value.field
        data = {"volume_level": round(float(raw) / 100, 2)} if field == "_volume_pct" else {field: raw}
        domain, service = value.service.split(".", 1)
        self.ha.call(domain, service, self.entity_id, data)

    def entity_updated(self) -> None:
        self.refresh_state()

    def action_dismiss(self) -> None:
        self.dismiss(None)
