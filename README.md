# ha-tui

Home Assistant dashboard and remote control for the terminal — a
[Textual](https://textual.textualize.io/) TUI plus a [Typer](https://typer.tiangolo.com/)/[Rich](https://rich.readthedocs.io/) CLI.

The dashboard is built the way the HA web frontend builds it: auto-generated
dashboards (the default **Overview**, the **Areas** dashboard, **Map**) are
never stored server-side, the browser generates them from the entity, device,
area and floor registries. `ha-tui` ports those strategies to Python
(`original-states`, `areas`, `area`, `areas-overview`, `map`), so the
Overview you get here has the same area cards, ordering, name prefixes
stripped, hidden/diagnostic entities filtered out, and display precision.

## Install

```bash
uv tool install .          # or: uv sync && uv run ha-tui
```

## Configure

Either put a `.env` in the directory you launch from:

```
HA_URL=https://homeassistant.local:8123
HA_TOKEN=<long-lived access token>
```

or run `ha-tui config init` to store them in `~/.config/ha-tui/config.toml`
(mode 600). Precedence: `--url/--token` flags › `HA_URL/HA_TOKEN` (also from
`./.env`) › `HASS_SERVER/HASS_TOKEN` (hass-cli's variables) › config file.

## The dashboard

```bash
ha-tui                  # opens the default (Overview) dashboard
ha-tui tui -D <url_path> -V <view>   # another dashboard / view
```

| Key | Action |
|---|---|
| `↑ ↓` / mouse | move through rows; crossing a card edge moves to the next card |
| `tab` | next card |
| `space` / `t` | quick action: toggle, run scene/script, press button, play/pause… |
| `enter` | open the entity's controls (more-info) |
| `/` or `ctrl+p` | search entities, cards and services |
| `:` | service console (prefilled with the selected entity) |
| `1` `2` `3` | Dashboard · All entities · Activity |
| `f` | filter all entities (`domain:light area:kitchen state:on`) |
| `r` | reload registries and dashboard |
| `b` | toggle the sidebar (jump to any card) |
| `q` | quit |

**More-info dialog** — live state, per-domain controls with keys
(`+`/`−` brightness, target temperature, position, volume, fan speed;
`[`/`]` colour temperature; `o`/`c`/`s` covers; `l`/`u` locks; `n`/`p`
media…), selects for modes/presets/effects/sources, direct value input
(brightness %, target, position, number/text helpers, alarm code),
a 24h history sparkline for numeric entities, and all attributes.

Live updates come from the `state_changed` subscription; the connection
reconnects automatically with backoff and resyncs state.

## CLI

```bash
ha-tui states [PATTERN] [-d DOMAIN] [-a AREA] [--json]
ha-tui state "kitchen light"           # names or fragments work if unambiguous
ha-tui on|off|toggle ENTITY...
ha-tui call light.turn_on light.kitchen -d brightness_pct=40
ha-tui call weather.get_forecasts weather.home -d type=daily   # prints the response
ha-tui services [DOMAIN] [--fields]
ha-tui areas
ha-tui dashboards
ha-tui dashboard [URL_PATH] [-V VIEW]  # the resolved dashboard as a tree
ha-tui watch [PATTERN] [-d DOMAIN]     # live state changes
ha-tui template "{{ states('sun.sun') }}"
```

## Development

```bash
uv sync
uv run pytest            # runs against a fake HA WebSocket server (tests/mock_ha.py)
uv run ruff check && uv run ruff format
```
