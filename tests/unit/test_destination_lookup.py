# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check the local car example's data processing and actual tool wiring."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.adapters.schemas.tools_schema import AdapterType

from examples.generic import destination_lookup
from examples.generic.tool_handlers import handle_find_destinations
from examples.generic.tools import build_tools_schema
from utils import PROJECT_ROOT, load_prompt_catalog, resolve_tools_available

MODULE_FILE = PROJECT_ROOT / "src/examples/generic/pipeline.py"


def test_nearest_category_matches_use_fixed_origin():
    """Filter the unsorted snapshot and put the closest match first."""
    result = destination_lookup.find_destinations("charging")
    assert result["source"] == "synthetic_training_data"
    assert result["snapshot_only"] is True
    assert result["origin"] == "Demo Junction"
    assert [record["name"] for record in result["destinations"]] == ["Harbor Charge", "Ridge Charge"]
    assert [record["distance_km"] for record in result["destinations"]] == [1.2, 4.8]


@pytest.mark.parametrize("category", ["charging", " CHARGING ", "Charging"])
def test_category_normalization(category):
    """Whitespace and case do not change the category search."""
    assert len(destination_lookup.find_destinations(category)["destinations"]) == 2


def test_maximum_distance_includes_boundary_and_does_not_relax_it():
    """Distance limits apply with category filtering and can produce no matches."""
    assert len(destination_lookup.find_destinations("charging", 1.2)["destinations"]) == 1
    assert destination_lookup.find_destinations("charging", 1)["destinations"] == []
    assert len(destination_lookup.find_destinations("parking", 1)["destinations"]) == 1


@pytest.mark.parametrize("category", [None, 1, {}, "", "coffee", "../charging"])
def test_invalid_category_does_not_read_catalog(monkeypatch, category):
    """Reject unsupported model arguments before accessing local data."""
    monkeypatch.setattr(destination_lookup, "DESTINATION_FIXTURES_PATH", SimpleNamespace(read_text=pytest.fail))
    assert destination_lookup.find_destinations(category)["error"] == "invalid_category"


@pytest.mark.parametrize("distance", [True, False, "3", [], 0, -1, float("nan"), float("inf")])
def test_invalid_maximum_distance(monkeypatch, distance):
    """The schema's numeric distance is independently checked by Python."""
    monkeypatch.setattr(destination_lookup, "DESTINATION_FIXTURES_PATH", SimpleNamespace(read_text=pytest.fail))
    assert destination_lookup.find_destinations("charging", distance)["error"] == "invalid_max_distance"


@pytest.mark.parametrize(
    "content",
    [
        None,
        "not JSON",
        "{}",
        '{"origin": "Demo", "destinations": null}',
        '{"origin": "Demo", "destinations": [{"name":"Bad", "category":"charging", "distance_km":-1}]}',
    ],
)
def test_missing_or_corrupt_catalog_returns_explicit_error(tmp_path, monkeypatch, content):
    """Unreadable or malformed snapshots cannot become invented matches."""
    fixture = tmp_path / "destinations.json"
    if content is not None:
        fixture.write_text(content)
    monkeypatch.setattr(destination_lookup, "DESTINATION_FIXTURES_PATH", fixture)
    assert destination_lookup.find_destinations("charging")["error"] == "destination_catalog_unavailable"


def test_tie_breaking_and_read_only_result(tmp_path, monkeypatch):
    """Name breaks distance ties, and mutations cannot change stored data."""
    fixture = tmp_path / "destinations.json"
    fixture.write_text(
        json.dumps(
            {
                "origin": "Demo",
                "destinations": [
                    {"name": "Zulu", "category": "fuel", "distance_km": 2},
                    {"name": "Alpha", "category": "fuel", "distance_km": 2},
                ],
            }
        )
    )
    monkeypatch.setattr(destination_lookup, "DESTINATION_FIXTURES_PATH", fixture)
    before = fixture.read_bytes()
    result = destination_lookup.find_destinations("fuel")
    assert [record["name"] for record in result["destinations"]] == ["Alpha", "Zulu"]
    result["destinations"][0]["name"] = "Changed"
    assert destination_lookup.find_destinations("fuel")["destinations"][0]["name"] == "Alpha"
    assert fixture.read_bytes() == before


def test_handler_delivers_real_result():
    """The registered callback returns filtered catalog data to the pipeline."""
    callback = AsyncMock()
    params = SimpleNamespace(arguments={"category": "charging", "max_distance_km": 3}, result_callback=callback)
    asyncio.run(handle_find_destinations(params))
    callback.assert_awaited_once()
    assert [record["name"] for record in callback.call_args.args[0]["destinations"]] == ["Harbor Charge"]


def test_car_preset_exposes_only_its_registered_tool():
    """The new tool is available to Dash while existing selections stay intact."""
    names = resolve_tools_available(MODULE_FILE, "car_assistant")
    assert names == ["find_destinations"]
    schema, registered = build_tools_schema(MODULE_FILE, names)
    assert registered == names
    tool = schema.custom_tools[AdapterType.OPENAI][0]
    assert tool["function"]["name"] == "find_destinations"
    assert tool["function"]["parameters"]["required"] == ["category"]
    assert "Dash" in load_prompt_catalog(MODULE_FILE)["car_assistant"]["content"]
    assert "find_destinations" not in resolve_tools_available(MODULE_FILE, "generic_assistant")
    assert resolve_tools_available(MODULE_FILE, "generic_assistant_without_tools") == []
    assert resolve_tools_available(MODULE_FILE, "enterprise_helpdesk") == ["lookup_ticket"]
