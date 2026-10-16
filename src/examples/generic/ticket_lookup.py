# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only ticket lookup using fictional records for the training help desk."""

import json
import re
from pathlib import Path

TICKET_FIXTURES_PATH = Path(__file__).with_name("ticket_fixtures.json")
SOURCE = "synthetic_training_data"


def lookup_ticket(ticket_id: object) -> dict:
    """Look up an IT ticket without network access or changes to its record."""
    if not isinstance(ticket_id, str):
        return {"source": SOURCE, "found": False, "error": "invalid_ticket_id"}
    normalized = re.sub(r"[\s-]", "", ticket_id).upper()
    if not re.fullmatch(r"(?:IT)?[0-9]{4}", normalized):
        return {"source": SOURCE, "found": False, "error": "invalid_ticket_id"}
    normalized = "IT-" + normalized.removeprefix("IT")
    try:
        records = json.loads(TICKET_FIXTURES_PATH.read_text(encoding="utf-8"))["tickets"]
        if not isinstance(records, dict):
            raise ValueError("Invalid ticket fixture catalog")
        record = records.get(normalized)
        if record is not None and not isinstance(record, dict):
            raise ValueError("Invalid ticket fixture record")
    except (OSError, ValueError, KeyError, TypeError):
        return {"source": SOURCE, "found": False, "error": "ticket_lookup_unavailable"}
    if record is None:
        return {"source": SOURCE, "found": False, "ticket_id": normalized, "error": "ticket_not_found"}
    return {"source": SOURCE, "found": True, "ticket": {"ticket_id": normalized, **record}}
