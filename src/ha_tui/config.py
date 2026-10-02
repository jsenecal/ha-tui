"""Connection settings: CLI flags > environment > config file."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("HA_TUI_CONFIG", "~/.config/ha-tui/config.toml")).expanduser()


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    url: str
    token: str
    verify_ssl: bool = True
    dashboard: str | None = None


def _env(*names: str) -> str | None:
    for name in names:
        if value := os.environ.get(name):
            return value
    return None


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=value lines from ./.env without overriding the real environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def read_file(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(
    url: str | None = None,
    token: str | None = None,
    insecure: bool | None = None,
    path: Path | None = None,
) -> Settings:
    load_dotenv()
    raw = read_file(path)
    url = url or _env("HA_URL", "HASS_SERVER") or raw.get("url")
    token = token or _env("HA_TOKEN", "HASS_TOKEN") or raw.get("token")
    if not url or not token:
        raise ConfigError(
            "No Home Assistant connection configured.\n"
            "Run `ha-tui config init`, or set HA_URL and HA_TOKEN "
            "(a Long-Lived Access Token from your HA profile > Security)."
        )
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Invalid URL {url!r}: must start with http:// or https://")
    verify_ssl = not insecure if insecure is not None else bool(raw.get("verify_ssl", True))
    return Settings(url=url.rstrip("/"), token=token, verify_ssl=verify_ssl, dashboard=raw.get("dashboard"))


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON string escaping is a valid subset of TOML basic strings.
    lines = [
        f"url = {json.dumps(settings.url)}",
        f"token = {json.dumps(settings.token)}",
        f"verify_ssl = {'true' if settings.verify_ssl else 'false'}",
    ]
    if settings.dashboard:
        lines.append(f"dashboard = {json.dumps(settings.dashboard)}")
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
