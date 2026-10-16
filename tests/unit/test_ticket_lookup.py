# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate deterministic demo ticket results and the server-side tool wiring."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.adapters.schemas.tools_schema import AdapterType

from examples.generic import ticket_lookup
from examples.generic.tool_handlers import handle_lookup_ticket
from examples.generic.tools import build_tools_schema
from utils import PROJECT_ROOT, load_prompt_catalog, resolve_tools_available

MODULE_FILE = PROJECT_ROOT / "src/examples/generic/pipeline.py"


@pytest.mark.parametrize("ticket_id", ["IT-1001", "it1001", "IT 1001", "1001", "IT 1 0 0 1"])
def test_ticket_id_variants_return_the_same_synthetic_record(ticket_id):
    """Normalize voice-friendly digit formatting without matching another ticket."""
    result = ticket_lookup.lookup_ticket(ticket_id)
    assert result["source"] == "synthetic_training_data"
    assert result["found"] is True
    assert result["ticket"]["ticket_id"] == "IT-1001"
    assert result["ticket"]["status"] == "Investigating"
    assert result["ticket"]["assigned_team"] == "Network Support"


@pytest.mark.parametrize("ticket_id", [None, 1001, {}, "", "../../IT-1001", "IT-10010", "INC-1001", "one thousand"])
def test_invalid_id_does_not_access_the_fixture(monkeypatch, ticket_id):
    """Malformed model arguments are rejected before reading any ticket data."""
    monkeypatch.setattr(ticket_lookup, "TICKET_FIXTURES_PATH", SimpleNamespace(read_text=pytest.fail))
    assert ticket_lookup.lookup_ticket(ticket_id) == {
        "source": "synthetic_training_data",
        "found": False,
        "error": "invalid_ticket_id",
    }


def test_unknown_ticket_does_not_invent_a_record():
    """A valid ID absent from the dataset reports not found."""
    result = ticket_lookup.lookup_ticket("IT-9999")
    assert result == {
        "source": "synthetic_training_data",
        "found": False,
        "ticket_id": "IT-9999",
        "error": "ticket_not_found",
    }


@pytest.mark.parametrize("content", [None, "not json", "{}", '{"tickets": []}', '{"tickets": {"IT-1001": []}}'])
def test_missing_or_corrupt_data_returns_unavailable(tmp_path, monkeypatch, content):
    """File failures are distinguishable from an unknown ticket and do not crash a turn."""
    fixture = tmp_path / "tickets.json"
    if content is not None:
        fixture.write_text(content)
    monkeypatch.setattr(ticket_lookup, "TICKET_FIXTURES_PATH", fixture)
    assert ticket_lookup.lookup_ticket("IT-1001")["error"] == "ticket_lookup_unavailable"


def test_results_match_fixtures_and_cannot_modify_them():
    """Every sample record is returned faithfully and lookup has no write effects."""
    fixture = ticket_lookup.TICKET_FIXTURES_PATH
    original = fixture.read_bytes()
    records = json.loads(original)["tickets"]
    for ticket_id, record in records.items():
        result = ticket_lookup.lookup_ticket(ticket_id)
        assert result["ticket"] == {"ticket_id": ticket_id, **record}
        result["ticket"]["status"] = "Changed by caller"
        assert ticket_lookup.lookup_ticket(ticket_id)["ticket"]["status"] == record["status"]
    assert fixture.read_bytes() == original


def test_handler_returns_result_through_pipecat_callback():
    """The actual registered handler delivers a usable tool result to the LLM."""
    callback = AsyncMock()
    params = SimpleNamespace(arguments={"ticket_id": "IT-1002"}, result_callback=callback)
    asyncio.run(handle_lookup_ticket(params))
    callback.assert_awaited_once()
    assert callback.call_args.args[0]["ticket"]["status"] == "Resolved"


def test_enterprise_prompt_exposes_only_the_ticket_tool():
    """The persona resolves through the existing catalog and registers the real handler."""
    names = resolve_tools_available(MODULE_FILE, "enterprise_helpdesk")
    assert names == ["lookup_ticket"]
    schema, registered = build_tools_schema(MODULE_FILE, names)
    assert registered == ["lookup_ticket"]
    tools = schema.custom_tools[AdapterType.OPENAI]
    assert [tool["function"]["name"] for tool in tools] == names
    assert "lookup_ticket" not in resolve_tools_available(MODULE_FILE, "generic_assistant")
    assert resolve_tools_available(MODULE_FILE, "generic_assistant_without_tools") == []
    assert "Nova" in load_prompt_catalog(MODULE_FILE)["enterprise_helpdesk"]["content"]
