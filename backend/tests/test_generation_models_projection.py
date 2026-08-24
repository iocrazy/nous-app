"""The canvas model picker needs to mark rows that run on the viewer's own
machine — but ``actual_provider`` itself is behind the 2026-08-14 leak
tripwire (upstream identity is private). The contract is therefore a derived
boolean: ``is_local`` in the payload, raw provider never in the projection.
"""

from __future__ import annotations

from app.api.canvases_router import _GENERATION_MODEL_PUBLIC_FIELDS
from app.repositories.mediahub_model_repository import _PUBLIC_COLS


def test_raw_provider_stays_out_of_the_projection():
    names = {c.key for c in _PUBLIC_COLS}
    assert "actual_provider" not in names
    assert "api_key" not in names


def test_generation_models_payload_carries_is_local_not_provider():
    assert "is_local" in _GENERATION_MODEL_PUBLIC_FIELDS
    assert "actual_provider" not in _GENERATION_MODEL_PUBLIC_FIELDS
