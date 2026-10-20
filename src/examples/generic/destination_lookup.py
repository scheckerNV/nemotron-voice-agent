# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search a fictional destination snapshot without network or GPS access."""

import json
import math
from pathlib import Path

DESTINATION_FIXTURES_PATH = Path(__file__).with_name("destination_fixtures.json")
CATEGORIES = {"charging", "fuel", "parking"}
SOURCE = "synthetic_training_data"


def find_destinations(category: object, max_distance_km: object = None) -> dict:
    """Return category matches ordered by fixed distance from the demo origin."""
    result = {"source": SOURCE, "snapshot_only": True}
    if not isinstance(category, str) or category.strip().lower() not in CATEGORIES:
        return {**result, "error": "invalid_category"}
    category = category.strip().lower()
    if max_distance_km is not None and (
        isinstance(max_distance_km, bool)
        or not isinstance(max_distance_km, (int, float))
        or (isinstance(max_distance_km, float) and not math.isfinite(max_distance_km))
        or max_distance_km <= 0
    ):
        return {**result, "error": "invalid_max_distance"}
    try:
        catalog = json.loads(DESTINATION_FIXTURES_PATH.read_text(encoding="utf-8"))
        origin, records = catalog["origin"], catalog["destinations"]
        if not isinstance(origin, str) or not origin or not isinstance(records, list):
            raise ValueError("Invalid destination catalog")
        matches = []
        for record in records:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("name"), str)
                or record.get("category") not in CATEGORIES
                or isinstance(record.get("distance_km"), bool)
                or not isinstance(record.get("distance_km"), (int, float))
                or not math.isfinite(record["distance_km"])
                or record["distance_km"] < 0
            ):
                raise ValueError("Invalid destination record")
            if record["category"] == category and (max_distance_km is None or record["distance_km"] <= max_distance_km):
                matches.append(record)
        matches.sort(key=lambda record: (record["distance_km"], record["name"]))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {**result, "error": "destination_catalog_unavailable"}
    return {**result, "origin": origin, "destinations": matches}
