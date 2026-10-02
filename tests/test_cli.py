from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ha_tui.cli import app

from .mock_ha import TOKEN, ThreadedMockHA

runner = CliRunner()


@pytest.fixture
def mock():
    with ThreadedMockHA() as m:
        yield m


def invoke(mock, *args: str):
    result = runner.invoke(app, ["--url", mock.url, "--token", TOKEN, *args], env={"COLUMNS": "200"})
    return result


def test_states_filters(mock):
    result = invoke(mock, "states", "--domain", "light")
    assert result.exit_code == 0, result.output
    assert "light.kitchen" in result.output and "light.living_room" in result.output
    assert "switch.coffee" not in result.output
    result = invoke(mock, "states", "--area", "kitchen")
    assert "switch.coffee" in result.output and "light.living_room" not in result.output


def test_state_by_name(mock):
    result = invoke(mock, "state", "thermostat")
    assert result.exit_code == 0, result.output
    assert "climate.thermostat" in result.output and "hvac_modes" in result.output


def test_ambiguous_name(mock):
    result = invoke(mock, "toggle", "kitchen")
    assert result.exit_code != 0
    assert "ambiguous" in result.output


def test_toggle_and_on(mock):
    result = invoke(mock, "toggle", "Kitchen Ceiling")
    assert result.exit_code == 0, result.output
    assert mock.states["light.kitchen"]["state"] == "off"
    invoke(mock, "on", "light.kitchen", "switch.coffee")
    assert mock.states["switch.coffee"]["state"] == "on"
    assert mock.calls[-1]["domain"] == "switch"


def test_call_with_data(mock):
    result = invoke(mock, "call", "climate.set_temperature", "climate.thermostat", "-d", "temperature=22.5")
    assert result.exit_code == 0, result.output
    assert mock.calls[-1]["service_data"] == {"temperature": 22.5}
    assert mock.calls[-1]["target"] == {"entity_id": ["climate.thermostat"]}


def test_call_with_response(mock):
    result = invoke(mock, "call", "weather.get_forecasts", "--json", '{"type": "daily"}')
    assert result.exit_code == 0, result.output
    assert '"forecast"' in result.output


def test_unknown_service(mock):
    result = invoke(mock, "call", "light.explode")
    assert result.exit_code != 0


def test_dashboard_tree(mock):
    result = invoke(mock, "dashboard")
    assert result.exit_code == 0, result.output
    assert "original-states" in result.output
    assert "Living Room" in result.output and "Coffee Maker" in result.output


def test_template(mock):
    result = invoke(mock, "template", "Hello {{ states('sensor.outside') }}")
    assert result.output.strip() == "Hello 12"


def test_missing_config(tmp_path, monkeypatch):
    for name in ("HA_URL", "HA_TOKEN", "HASS_SERVER", "HASS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("ha_tui.config.CONFIG_PATH", tmp_path / "none.toml")
    result = runner.invoke(app, ["states"])
    assert result.exit_code == 2
