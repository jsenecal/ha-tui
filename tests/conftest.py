from __future__ import annotations

import pytest

from ha_tui.client import HAClient
from ha_tui.hass import Hass

from .mock_ha import TOKEN, MockHA


@pytest.fixture
async def mock_ha():
    mock = MockHA()
    await mock.start()
    yield mock
    await mock.stop()


@pytest.fixture
async def client(mock_ha: MockHA):
    c = HAClient(mock_ha.url, TOKEN)
    await c.connect()
    yield c
    await c.close()


@pytest.fixture
async def hass(client: HAClient) -> Hass:
    return await Hass.load(client)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never let a developer's .env, HA_* / HASS_* variables or config file reach the tests."""
    monkeypatch.chdir(tmp_path)
    for name in ("HA_URL", "HA_TOKEN", "HASS_SERVER", "HASS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("ha_tui.config.CONFIG_PATH", tmp_path / "config.toml")
