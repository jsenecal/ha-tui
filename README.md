# ha-tui

Home Assistant dashboard and remote control for the terminal — a
[Textual](https://textual.textualize.io/) TUI plus a [Typer](https://typer.tiangolo.com/)/[Rich](https://rich.readthedocs.io/) CLI.

The dashboard is built the way the HA web frontend builds it. Generated
dashboards are never stored server-side; the browser builds them from the
entity, device, area and floor registries, so `ha-tui` ports those strategies
to Python:

- **Home** (the new Overview, `home` strategy): favourites and suggested
  controls (usage prediction), live summaries (repairs, updates, lights,
  climate, security, media, maintenance, weather), and area cards grouped by
  floor. Each area opens its own view (lights with an all on/off row, climate,
  security, media, scenes, then the rest grouped by device), and each summary
  opens its panel (Lights, Climate, Security, Maintenance, Media players). This
  is the default when your instance has the Home panel.
- **Legacy Overview** (`original-states`), **Areas** and **Map**, plus any
  stored Lovelace dashboard.

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
ha-tui                  # opens Home (the new Overview), or the default dashboard
ha-tui tui -D default   # the legacy auto-generated Overview
ha-tui tui -D <url_path> -V <view>   # another dashboard / view
```

| Key | Action |
|---|---|
| `↑ ↓` / mouse | move through rows; crossing a card edge moves to the next card |
| `tab` | next card |
| `space` / `t` | quick action: toggle, run scene/script, press button, play/pause… |
| `enter` | open the entity's controls; on an area or summary, open its view |
| `h` | history of the selected entity (also in the controls dialog and the entities list) |
| `esc` | back to the previous view |
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

**History** (`h`) — numeric sensors get a line chart with min / time-weighted
average / max; everything else gets a coloured state timeline (brief events
stay visible), time spent per state, and the list of changes with durations.
`1`–`6` switch between 1h, 6h, 24h, 3d, 7d and 30d; 30 days uses the
recorder's hourly long-term statistics (mean with min–max) when the sensor has
them, since raw history is purged after 10 days by default. Updates live.

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
ha-tui history ENTITY [-r 1h|6h|24h|3d|7d|30d]   # chart, or timeline + changes
```

## Development

```bash
uv sync
uv run pytest            # runs against a fake HA WebSocket server (tests/mock_ha.py)
uv run ruff check && uv run ruff format
```
