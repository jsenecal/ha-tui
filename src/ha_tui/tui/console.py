"""Service-call console: any service, any target, arbitrary data."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester, SuggestFromList
from textual.widgets import Button, Input, Label, Static, TextArea

if TYPE_CHECKING:
    from .app import HATuiApp


class EntityListSuggester(Suggester):
    """Completes the last comma-separated entity id."""

    def __init__(self, entity_ids: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.entity_ids = sorted(entity_ids)

    async def get_suggestion(self, value: str) -> str | None:
        head, _, last = value.rpartition(",")
        last = last.lstrip()
        if not last:
            return None
        for eid in self.entity_ids:
            if eid.startswith(last):
                return f"{head}, {eid}" if head else eid
        return None


def parse_data(text: str) -> dict[str, Any]:
    """JSON object, or `key: value` lines (values parsed as JSON when possible)."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Data must be a JSON object")
        return data
    data: dict[str, Any] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Expected 'key: value', got {line!r}")
        key, value = line.split(":", 1)
        value = value.strip()
        try:
            data[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            data[key.strip()] = value
    return data


class ServiceConsole(ModalScreen[None]):
    DEFAULT_CSS = """
    ServiceConsole { align: center middle; }
    ServiceConsole > #dialog {
        width: 100; max-width: 95%; height: auto; max-height: 92%;
        border: thick $accent 60%; background: $surface; padding: 1 2;
    }
    ServiceConsole Label { margin-top: 1; color: $text-muted; }
    ServiceConsole TextArea { height: 7; }
    ServiceConsole #doc { height: auto; max-height: 12; margin-top: 1; color: $text-muted; }
    ServiceConsole #buttons { height: auto; margin-top: 1; }
    ServiceConsole #result { height: auto; max-height: 12; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("ctrl+s", "call", "Call service", priority=True)]

    def __init__(self, service: str = "", target: str = "") -> None:
        super().__init__()
        self.initial_service = service
        self.initial_target = target

    @property
    def ha(self) -> HATuiApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        hass = self.ha.hass
        with Vertical(id="dialog"):
            yield Static("[b]Call a service[/b]  [dim]ctrl+s to call · esc to close[/dim]")
            yield Label("Service (domain.service)")
            yield Input(
                self.initial_service,
                id="service",
                placeholder="light.turn_on",
                suggester=SuggestFromList(hass.service_names(), case_sensitive=False),
            )
            yield Label("Target entities (comma-separated)")
            yield Input(
                self.initial_target,
                id="target",
                placeholder="light.kitchen, light.office",
                suggester=EntityListSuggester(list(hass.states)),
            )
            yield Label("Data: JSON object or `key: value` lines")
            yield TextArea(id="data", language=None, soft_wrap=True)
            yield Static(id="doc")
            with Horizontal(id="buttons"):
                yield Button("Call service", id="call", variant="primary")
            yield Static(id="result")

    def on_mount(self) -> None:
        self.query_one("#service" if not self.initial_service else "#target", Input).focus()
        self._show_doc(self.initial_service)

    @on(Input.Changed, "#service")
    def _service_changed(self, event: Input.Changed) -> None:
        self._show_doc(event.value)

    def _show_doc(self, value: str) -> None:
        doc = self.query_one("#doc", Static)
        domain, _, name = value.partition(".")
        spec = self.ha.hass.services.get(domain, {}).get(name)
        if not spec:
            doc.update("")
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(style="yellow", no_wrap=True)
        table.add_column()
        fields: dict[str, Any] = {}
        for fname, fspec in (spec.get("fields") or {}).items():
            if isinstance(fspec, dict) and isinstance(fspec.get("fields"), dict):
                fields.update(fspec["fields"])
            else:
                fields[fname] = fspec
        for fname, fspec in fields.items():
            fspec = fspec if isinstance(fspec, dict) else {}
            example = f"  e.g. {fspec['example']}" if "example" in fspec else ""
            req = Text(" *", style="red") if fspec.get("required") else Text("")
            table.add_row(Text(fname) + req, Text(f"{fspec.get('description', '')}{example}"))
        header = Text(spec.get("description") or spec.get("name") or "", style="italic")
        doc.update(_stack(header, table))

    @on(Button.Pressed, "#call")
    def _pressed(self) -> None:
        self.action_call()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "service":
            self.query_one("#target", Input).focus()
        else:
            self.query_one("#data", TextArea).focus()

    def action_call(self) -> None:
        service = self.query_one("#service", Input).value.strip()
        domain, _, name = service.partition(".")
        result = self.query_one("#result", Static)
        if name not in self.ha.hass.services.get(domain, {}):
            result.update(Text(f"Unknown service {service!r}", style="red"))
            return
        try:
            data = parse_data(self.query_one("#data", TextArea).text)
        except ValueError as exc:
            result.update(Text(str(exc), style="red"))
            return
        targets = [t.strip() for t in self.query_one("#target", Input).value.split(",") if t.strip()]
        self._call(domain, name, targets, data)

    @work(exclusive=True)
    async def _call(self, domain: str, name: str, targets: list[str], data: dict[str, Any]) -> None:
        result = self.query_one("#result", Static)
        spec = self.ha.hass.services[domain][name]
        wants_response = bool(spec.get("response"))
        try:
            response = await self.ha.client.call_service(
                domain, name, data, {"entity_id": targets} if targets else None, return_response=wants_response
            )
        except Exception as exc:
            result.update(Text(f"✗ {exc}", style="red"))
            return
        text = Text(f"✓ {domain}.{name} called", style="green")
        if wants_response and response and response.get("response") is not None:
            text.append("\n" + json.dumps(response["response"], indent=2, ensure_ascii=False), style="default")
        result.update(text)

    def action_dismiss(self) -> None:
        self.dismiss(None)


def _stack(*renderables: Any) -> Table:
    grid = Table.grid()
    for r in renderables:
        grid.add_row(r)
    return grid
