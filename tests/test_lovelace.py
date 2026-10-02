from __future__ import annotations

from ha_tui.client import HAClient
from ha_tui.hass import Hass
from ha_tui.lovelace.resolve import build_dashboard, convert_view, load_dashboard
from ha_tui.lovelace.strategies import strip_prefix


def _cards(view):
    return [(c.title, [i.entity_id or i.kind for i in c.items]) for c in view.cards]


async def test_default_dashboard_falls_back_to_original_states(client: HAClient, hass: Hass):
    dash = await load_dashboard(client, hass, None)
    assert dash.strategy == "original-states"
    assert [v.title for v in dash.views] == ["Home"]
    assert _cards(dash.views[0]) == [
        ("Living Room", ["cover.blinds", "light.living_room", "scene.movie", "climate.thermostat", "media_player.tv"]),
        ("Kitchen", ["light.kitchen", "switch.coffee", "sensor.kitchen_temp"]),
        (None, ["person.alice"]),
        ("Binary sensor", ["binary_sensor.door"]),
        ("Sensor", ["sensor.outside"]),
    ]


async def test_original_states_strips_area_prefix_from_names(client: HAClient, hass: Hass):
    dash = await load_dashboard(client, hass, None)
    kitchen = dash.views[0].cards[1]
    assert [i.name for i in kitchen.items] == ["Ceiling", "Coffee Maker", "Temperature"]


async def test_original_states_options(hass: Hass):
    raw = {
        "strategy": {
            "type": "original-states",
            "hide_entities_without_area": True,
            "areas": {"order": ["kitchen"], "hidden": ["living_room"]},
        }
    }
    view = build_dashboard(raw, hass, None, "Overview").views[0]
    assert [c.title for c in view.cards] == ["Kitchen"]


async def test_areas_dashboard(hass: Hass):
    dash = build_dashboard({"strategy": {"type": "areas"}}, hass, "areas", "Areas")
    home, *areas = dash.views
    assert [(c.title, [i.area_id for i in c.items]) for c in home.cards] == [
        ("Main Floor", ["living_room", "kitchen"]),
        ("Other areas", ["garage"]),
    ]
    assert all(v.subview for v in areas)
    kitchen = dash.view_by_path("areas-kitchen")
    assert kitchen is not None
    assert _cards(kitchen) == [("Lights", ["light.kitchen"]), ("Others", ["switch.coffee"])]
    assert [b.entity_id for b in kitchen.badges] == ["sensor.kitchen_temp"]
    assert kitchen.cards[1].items[0].name == "Coffee Maker"


async def test_custom_cards_headings_and_markdown(client: HAClient, hass: Hass):
    dash = await load_dashboard(client, hass, "custom")
    main = dash.views[0]
    assert [b.entity_id for b in main.badges] == ["sensor.outside"]
    assert _cards(main) == [(None, ["markdown"]), ("LIGHTS", ["light.kitchen", "light.living_room"])]
    lights = main.cards[1].items
    assert lights[0].name == "Ceiling"
    assert lights[1].name is None  # JS template names are not shown verbatim
    # A view-level strategy inside a stored dashboard is generated too.
    assert [c.title for c in dash.views[1].cards] == ["Main Floor", "Other areas"]


def test_convert_sections_and_entities_rows(hass: Hass):
    raw = {
        "type": "sections",
        "sections": [
            {"cards": [{"type": "heading", "heading": "Climate"}, {"type": "tile", "entity": "climate.thermostat"}]},
            {
                "cards": [
                    {
                        "type": "entities",
                        "title": "Misc",
                        "entities": [
                            "switch.coffee",
                            {"type": "section", "label": "Covers"},
                            {"entity": "cover.blinds", "name": "Blinds!"},
                            {"type": "conditional", "row": {"entity": "light.kitchen"}},
                            {"type": "divider"},
                        ],
                    },
                    {
                        "type": "vertical-stack",
                        "cards": [{"type": "glance", "entities": ["sensor.outside", "not an id"]}],
                    },
                ]
            },
        ],
    }
    view = convert_view(raw, hass, 0)
    assert _cards(view) == [
        ("Climate", ["climate.thermostat"]),
        ("Misc", ["switch.coffee"]),
        ("Covers", ["cover.blinds", "light.kitchen", "sensor.outside"]),
    ]
    assert view.cards[2].items[0].name == "Blinds!"


def test_strip_prefix_matches_frontend():
    assert strip_prefix("Kitchen Ceiling light", "kitchen") == "Ceiling light"
    assert strip_prefix("Kitchen: lamp", "kitchen") == "Lamp"
    # The frontend tries " " before " - ", so the dash survives; mirror that quirk.
    assert strip_prefix("Kitchen - iPhone charger", "kitchen") == "- iPhone charger"
    assert strip_prefix("Kitchen", "kitchen") is None
    assert strip_prefix("Office lamp", "kitchen") is None
