"""`ha-tui` command line: launches the dashboard, or runs one-shot commands."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from . import __version__
from .client import HAClient, HAError
from .config import CONFIG_PATH, ConfigError, Settings, load_settings, read_file, save_settings
from .format import domain_of, humanize_time, icon, styled_state
from .hass import Hass
from .lovelace.resolve import list_dashboards, load_dashboard

app = typer.Typer(
    help="Home Assistant dashboard and remote control for the terminal.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
config_app = typer.Typer(help="Manage the connection settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")

console = Console()
err = Console(stderr=True)
T = TypeVar("T")


class Ctx:
    def __init__(self, url: str | None, token: str | None, insecure: bool | None) -> None:
        self.url, self.token, self.insecure = url, token, insecure

    def settings(self) -> Settings:
        try:
            return load_settings(self.url, self.token, self.insecure)
        except ConfigError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc


def _version(value: bool) -> None:
    if value:
        console.print(f"ha-tui {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    url: Annotated[str | None, typer.Option(help="Home Assistant URL (env: HA_URL).", show_default=False)] = None,
    token: Annotated[
        str | None, typer.Option(help="Long-Lived Access Token (env: HA_TOKEN).", show_default=False)
    ] = None,
    insecure: Annotated[bool | None, typer.Option("--insecure/--verify", help="Skip TLS verification.")] = None,
    debug: Annotated[bool, typer.Option(help="Write debug logs to ha-tui.log.")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")] = False,
) -> None:
    """Run without a command to open the dashboard."""
    if debug:
        logging.basicConfig(filename="ha-tui.log", level=logging.DEBUG)
    ctx.obj = Ctx(url, token, insecure)
    if ctx.invoked_subcommand is None:
        ctx.invoke(tui, ctx=ctx)


def run(ctx: typer.Context, fn: Callable[[HAClient], Awaitable[T]]) -> T:
    settings: Settings = ctx.obj.settings()

    async def go() -> T:
        client = HAClient(settings.url, settings.token, settings.verify_ssl)
        await client.connect()
        try:
            return await fn(client)
        finally:
            await client.close()

    try:
        return asyncio.run(go())
    except HAError as exc:
        err.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        raise typer.Exit(130) from None


def resolve_entity(hass: Hass, query: str) -> str:
    """Accept an entity_id, an exact friendly name, or an unambiguous fragment."""
    if query in hass.states:
        return query
    q = query.casefold()
    exact = [e for e in hass.states if hass.name(e).casefold() == q]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [e for e in hass.states if q in e.casefold() or q in hass.name(e).casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise typer.BadParameter(f"No entity matches {query!r}")
    listing = "\n".join(f"  {e}  ({hass.name(e)})" for e in partial[:15])
    more = f"\n  … and {len(partial) - 15} more" if len(partial) > 15 else ""
    raise typer.BadParameter(f"{query!r} is ambiguous:\n{listing}{more}")


def _entity_matches(hass: Hass, eid: str, pattern: str | None, domain: str | None, area: str | None) -> bool:
    if domain and domain_of(eid) != domain:
        return False
    if area:
        a = area.casefold()
        if a not in ((hass.area_id(eid) or "").casefold(), (hass.area_name(eid) or "").casefold()):
            return False
    if pattern:
        p = pattern.casefold()
        return p in eid.casefold() or p in hass.name(eid).casefold()
    return True


def parse_data(pairs: list[str] | None, raw_json: str | None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(raw_json) if raw_json else {}
    for pair in pairs or []:
        if "=" not in pair:
            raise typer.BadParameter(f"Expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
    return data


# ---------------------------------------------------------------------- #
@app.command()
def tui(
    ctx: typer.Context,
    dashboard: Annotated[str | None, typer.Option("--dashboard", "-D", help="Dashboard url_path to open.")] = None,
    view: Annotated[str | None, typer.Option("--view", "-V", help="View path to open.")] = None,
) -> None:
    """Open the interactive dashboard (default)."""
    from .tui.app import HATuiApp

    settings = ctx.obj.settings()
    HATuiApp(settings, dashboard=dashboard or settings.dashboard, view=view).run()


@config_app.command("init")
def config_init(
    url: Annotated[str | None, typer.Option(help="Home Assistant URL.")] = None,
    insecure: Annotated[bool, typer.Option(help="Skip TLS verification.")] = False,
) -> None:
    """Interactively store the URL and token in the config file."""
    existing = read_file()
    url = url or typer.prompt("Home Assistant URL", default=existing.get("url") or "http://homeassistant.local:8123")
    token = typer.prompt("Long-Lived Access Token", hide_input=True)
    settings = Settings(url=url.rstrip("/"), token=token, verify_ssl=not insecure)

    async def check() -> str | None:
        client = HAClient(settings.url, settings.token, settings.verify_ssl)
        await client.connect()
        await client.close()
        return client.ha_version

    try:
        version = asyncio.run(check())
    except HAError as exc:
        err.print(f"[red]Could not connect:[/red] {exc}")
        raise typer.Exit(1) from exc
    path = save_settings(settings)
    console.print(f"[green]✓[/green] Connected to Home Assistant {version}. Saved to {path}")


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Show the effective settings (token masked)."""
    s = ctx.obj.settings()
    table = Table.grid(padding=(0, 2))
    table.add_row("config file", str(CONFIG_PATH) + ("" if Path(CONFIG_PATH).exists() else " (missing)"))
    table.add_row("url", s.url)
    table.add_row("token", f"{s.token[:6]}…{s.token[-4:]}" if len(s.token) > 12 else "***")
    table.add_row("verify_ssl", str(s.verify_ssl))
    table.add_row("dashboard", s.dashboard or "(default)")
    console.print(table)


@app.command()
def states(
    ctx: typer.Context,
    pattern: Annotated[str | None, typer.Argument(help="Substring of entity_id or name.")] = None,
    domain: Annotated[str | None, typer.Option("--domain", "-d", help="Only this domain.")] = None,
    area: Annotated[str | None, typer.Option("--area", "-a", help="Only this area (id or name).")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output raw JSON.")] = False,
) -> None:
    """List entities and their states."""

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        ids = sorted(e for e in hass.states if _entity_matches(hass, e, pattern, domain, area))
        if as_json:
            console.print_json(data=[hass.states[e] for e in ids])
            return
        table = Table(box=None, header_style="bold", pad_edge=False)
        table.add_column("", width=2)
        table.add_column("Entity", style="dim")
        table.add_column("Name")
        table.add_column("Area", style="magenta")
        table.add_column("State", overflow="fold")
        table.add_column("Changed", style="dim", justify="right")
        for eid in ids:
            st = hass.states[eid]
            table.add_row(
                icon(st, eid),
                eid,
                hass.name(eid),
                hass.area_name(eid) or "",
                styled_state(st, eid),
                humanize_time(st.get("last_changed")),
            )
        console.print(table)
        console.print(f"[dim]{len(ids)} entities[/dim]")

    run(ctx, go)


@app.command()
def state(
    ctx: typer.Context,
    entity: Annotated[str, typer.Argument(help="Entity id or name.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output raw JSON.")] = False,
) -> None:
    """Show one entity with all its attributes."""

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        eid = resolve_entity(hass, entity)
        st = hass.states[eid]
        if as_json:
            console.print_json(data=st)
            return
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column(overflow="fold")
        grid.add_row("state", styled_state(st, eid))
        grid.add_row("raw", str(st.get("state")))
        if area := hass.area_name(eid):
            grid.add_row("area", area)
        grid.add_row("changed", f"{humanize_time(st.get('last_changed'))}  [dim]{st.get('last_changed')}[/dim]")
        for key, value in sorted(st.get("attributes", {}).items()):
            if key != "friendly_name":
                grid.add_row(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        console.print(Panel(grid, title=f"{icon(st, eid)} {hass.name(eid)}", subtitle=eid, expand=False))

    run(ctx, go)


def _quick(ctx: typer.Context, entities: list[str], service: str) -> None:
    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        ids = [resolve_entity(hass, e) for e in entities]
        for eid in ids:
            domain = domain_of(eid)
            svc_domain = domain if service in hass.services.get(domain, {}) else "homeassistant"
            await client.call_service(svc_domain, service, target={"entity_id": eid})
        await asyncio.sleep(0.4)
        latest = {st["entity_id"]: st for st in await client.get_states()}
        for eid in ids:
            st = latest.get(eid)
            console.print(f"{icon(st, eid)} {hass.name(eid)} [dim]{eid}[/dim] → ", styled_state(st, eid))

    run(ctx, go)


@app.command()
def on(ctx: typer.Context, entities: Annotated[list[str], typer.Argument(help="Entity ids or names.")]) -> None:
    """Turn entities on."""
    _quick(ctx, entities, "turn_on")


@app.command()
def off(ctx: typer.Context, entities: Annotated[list[str], typer.Argument(help="Entity ids or names.")]) -> None:
    """Turn entities off."""
    _quick(ctx, entities, "turn_off")


@app.command()
def toggle(ctx: typer.Context, entities: Annotated[list[str], typer.Argument(help="Entity ids or names.")]) -> None:
    """Toggle entities."""
    _quick(ctx, entities, "toggle")


@app.command()
def call(
    ctx: typer.Context,
    service: Annotated[str, typer.Argument(help="domain.service, e.g. light.turn_on")],
    entities: Annotated[list[str] | None, typer.Argument(help="Target entity ids or names.")] = None,
    data: Annotated[
        list[str] | None, typer.Option("--data", "-d", help="key=value (value parsed as JSON if possible).")
    ] = None,
    raw_json: Annotated[str | None, typer.Option("--json", help="Service data as a JSON object.")] = None,
    area: Annotated[list[str] | None, typer.Option("--area", "-a", help="Target area id.")] = None,
) -> None:
    """Call any service. Prints the response if the service returns one."""
    if "." not in service:
        raise typer.BadParameter("Service must look like domain.service")
    domain, name = service.split(".", 1)
    payload = parse_data(data, raw_json)

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        if name not in hass.services.get(domain, {}):
            raise typer.BadParameter(f"Unknown service {service}")
        target: dict[str, Any] = {}
        if entities:
            target["entity_id"] = [resolve_entity(hass, e) for e in entities]
        if area:
            target["area_id"] = area
        wants_response = bool(hass.services[domain][name].get("response"))
        result = await client.call_service(domain, name, payload, target or None, return_response=wants_response)
        console.print(f"[green]✓[/green] {service}")
        if wants_response and result and result.get("response") is not None:
            console.print_json(data=result["response"])

    run(ctx, go)


@app.command()
def services(
    ctx: typer.Context,
    domain: Annotated[str | None, typer.Argument(help="Only this domain.")] = None,
    fields: Annotated[bool, typer.Option("--fields", "-f", help="Show each service's fields.")] = False,
) -> None:
    """List available services."""

    async def go(client: HAClient) -> None:
        svcs = await client.get_services()
        tree = Tree("[bold]Services[/bold]")
        for d in sorted(svcs):
            if domain and d != domain:
                continue
            node = tree.add(f"[bold cyan]{d}[/bold cyan]")
            for name, spec in sorted(svcs[d].items()):
                desc = spec.get("description") or spec.get("name") or ""
                leaf = node.add(f"{d}.[bold]{name}[/bold]  [dim]{desc}[/dim]")
                if fields:
                    for fname, fspec in (spec.get("fields") or {}).items():
                        if isinstance(fspec, dict) and "fields" in fspec:  # collapsed section
                            for sub, sspec in fspec["fields"].items():
                                leaf.add(f"[yellow]{sub}[/yellow]  [dim]{sspec.get('description', '')}[/dim]")
                        else:
                            req = " [red]*[/red]" if isinstance(fspec, dict) and fspec.get("required") else ""
                            desc = fspec.get("description", "") if isinstance(fspec, dict) else ""
                            leaf.add(f"[yellow]{fname}[/yellow]{req}  [dim]{desc}[/dim]")
        console.print(tree)

    run(ctx, go)


@app.command()
def areas(ctx: typer.Context) -> None:
    """List floors and areas with entity counts."""

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        counts: dict[str | None, int] = {}
        for eid in hass.states:
            counts[hass.area_id(eid)] = counts.get(hass.area_id(eid), 0) + 1
        tree = Tree("[bold]Home[/bold]")
        floors = [(f["floor_id"], f["name"]) for f in hass.floors.values()] + [(None, "No floor")]
        for floor_id, floor_name in floors:
            floor_areas = [a for a in hass.areas.values() if a.get("floor_id") == floor_id]
            if not floor_areas:
                continue
            node = tree.add(f"[bold cyan]{floor_name}[/bold cyan]")
            for a in floor_areas:
                node.add(f"{a['name']} [dim]{a['area_id']} · {counts.get(a['area_id'], 0)} entities[/dim]")
        tree.add(f"[dim]{counts.get(None, 0)} entities without an area[/dim]")
        console.print(tree)

    run(ctx, go)


@app.command()
def dashboards(ctx: typer.Context) -> None:
    """List Lovelace dashboards."""

    async def go(client: HAClient) -> None:
        table = Table(box=None, header_style="bold")
        table.add_column("url_path")
        table.add_column("Title")
        for url_path, title in await list_dashboards(client):
            table.add_row(url_path or "[dim](default)[/dim]", title)
        console.print(table)

    run(ctx, go)


@app.command()
def dashboard(
    ctx: typer.Context,
    url_path: Annotated[str | None, typer.Argument(help="Dashboard url_path (default dashboard if omitted).")] = None,
    view: Annotated[str | None, typer.Option("--view", "-V", help="Only this view path.")] = None,
    states_: Annotated[bool, typer.Option("--states/--no-states", help="Show current states.")] = True,
) -> None:
    """Print a dashboard the way the TUI resolves it (strategies included)."""

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        dash = await load_dashboard(client, hass, url_path)
        label = f"[bold]{dash.title}[/bold]" + (f" [dim](strategy: {dash.strategy})[/dim]" if dash.strategy else "")
        tree = Tree(label)
        if dash.notice:
            tree.add(f"[yellow]{dash.notice}[/yellow]")
        for v in dash.views:
            if view and v.path != view:
                continue
            vnode = tree.add(
                f"[bold cyan]{v.title}[/bold cyan] [dim]/{v.path}{' · subview' if v.subview else ''}[/dim]"
            )
            if v.error:
                vnode.add(f"[red]{v.error}[/red]")
            for b in v.badges:
                if b.entity_id:
                    vnode.add(
                        Text.assemble(
                            "badge ",
                            hass.name(b.entity_id),
                            " ",
                            styled_state(hass.states.get(b.entity_id), b.entity_id),
                        )
                    )
            for card in v.cards:
                cnode = vnode.add(f"[bold]{card.title or '·'}[/bold]")
                for item in card.items:
                    if item.kind == "entity" and item.entity_id:
                        st = hass.states.get(item.entity_id)
                        line = Text.assemble(f"{icon(st, item.entity_id)} ", item.name or hass.name(item.entity_id))
                        if states_:
                            line.append("  ")
                            line.append_text(styled_state(st, item.entity_id))
                        cnode.add(line)
                    elif item.kind == "area" and item.area_id:
                        cnode.add(f"🏠 {hass.areas[item.area_id]['name']} [dim]→ {item.path}[/dim]")
                    elif item.kind == "markdown" and item.text:
                        first = item.text.strip().splitlines()[0]
                        cnode.add(f"[dim]markdown:[/dim] {first[:70]}")
        console.print(tree)

    run(ctx, go)


@app.command()
def template(ctx: typer.Context, tpl: Annotated[str, typer.Argument(help="Jinja template to render.")]) -> None:
    """Render a template on the server."""
    console.print(run(ctx, lambda c: c.render_template(tpl)), markup=False, highlight=False)


@app.command()
def watch(
    ctx: typer.Context,
    pattern: Annotated[str | None, typer.Argument(help="Substring of entity_id or name.")] = None,
    domain: Annotated[str | None, typer.Option("--domain", "-d", help="Only this domain.")] = None,
    area: Annotated[str | None, typer.Option("--area", "-a", help="Only this area (id or name).")] = None,
    attributes: Annotated[bool, typer.Option("--attributes", help="Also show attribute-only changes.")] = False,
    limit: Annotated[int, typer.Option(help="Rows kept on screen.")] = 30,
) -> None:
    """Stream live state changes (Ctrl+C to stop)."""

    async def go(client: HAClient) -> None:
        hass = await Hass.load(client)
        rows: list[tuple[str, str, Text, Text]] = []

        def render() -> Table:
            table = Table(box=None, header_style="bold", expand=True)
            table.add_column("Time", style="dim", width=8)
            table.add_column("Entity")
            table.add_column("From", justify="right")
            table.add_column("To")
            for row in rows[-limit:]:
                table.add_row(*row)
            return table

        with Live(render(), console=console, auto_refresh=False) as live:

            def on_change(new: dict[str, Any], old: dict[str, Any] | None) -> None:
                eid = new["entity_id"]
                hass.apply_state(new)
                if not _entity_matches(hass, eid, pattern, domain, area):
                    return
                if not attributes and old and old.get("state") == new.get("state"):
                    return
                rows.append(
                    (
                        datetime.now().strftime("%H:%M:%S"),
                        f"{icon(new, eid)} {hass.name(eid)} [dim]{eid}[/dim]",
                        styled_state(old, eid) if old else Text("—", style="dim"),
                        styled_state(new, eid) if new.get("state") is not None else Text("removed", style="red"),
                    )
                )
                live.update(render(), refresh=True)

            await client.subscribe_states(on_change)
            console.print("[dim]Watching… Ctrl+C to stop[/dim]")
            await asyncio.Event().wait()

    run(ctx, go)
