"""A ``nous_models`` row as ``NousModelRepository.list_enabled`` returns it.

Canvas picker tests used to stub ``list_enabled`` with two or three keys.
Once the picker routes declared their public projection, those rows were no
longer valid responses — they never were the real shape. Start from here.
"""

from __future__ import annotations

from typing import Any, Dict

from app.repositories import nous_model_repository as repo
from tests.api.wire_parity import SAMPLE_TS


def list_enabled_row(**overrides: Any) -> Dict[str, Any]:
    """Every public column plus the derived ``is_local`` bit."""
    row: Dict[str, Any] = {
        "id": 7_300_000_000_000_000_777,
        "name": "nous-sample-model",
        "display_name": "Sample Model",
        "actual_model": "sample-model-2026",
        "type": "image",
        "pricing_type": "per_call",
        "pricing_value": 0,
        "sort_order": 10,
        "last_test_status": "ok",
        "last_tested_at": SAMPLE_TS,
        "last_test_code": None,
    }
    assert set(row) == {c.key for c in repo._PUBLIC_COLS}
    row = repo._parity(row)
    row["is_local"] = False
    row.update(overrides)
    return row
