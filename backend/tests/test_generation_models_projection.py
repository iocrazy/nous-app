"""The model pickers need to mark rows that run on the viewer's own machine —
but ``actual_provider`` itself is behind the 2026-08-14 leak tripwire
(upstream identity is private). The contract is therefore a derived boolean:
``is_local`` in the payload, raw provider never in the projection.

Since spec 2026-09-25 §3.8 every picker maps from ``platform_models`` in
``GET /ai/settings`` (``PlatformModel.mapping_entry``), so that projection is
the one pinned here, alongside the public catalog columns.
"""

from __future__ import annotations

from app.repositories.nous_model_repository import _PUBLIC_COLS
from app.services.ai.platform_provider import PlatformModel


def _model() -> PlatformModel:
    return PlatformModel(
        id=1900000000000000001,
        name="codex-local-image",
        display_name="GPT Image (Local)",
        actual_model="gpt-image-2",
        type="image",
        status="ok",
        is_local=True,
        actual_provider="codex-local",
        pricing_type="per_call",
        pricing_value=0.0,
        context_window_tokens=None,
        generatable=True,
        sort_order=0,
        last_tested_at=None,
        last_test_code=None,
    )


def test_raw_provider_stays_out_of_the_projection():
    names = {c.key for c in _PUBLIC_COLS}
    assert "actual_provider" not in names
    assert "api_key" not in names


def test_pickers_carry_admin_label_and_status_not_probe_text():
    """2026-09-24: pickers name a row by ``actual_model``, the same string the
    admin AI Models card shows. The probe's failure TEXT stays out."""
    entry = _model().mapping_entry()
    assert entry["actual_model"] == "gpt-image-2"
    assert "status" in entry
    assert "last_test_detail" not in entry


def test_platform_payloads_carry_is_local_not_provider():
    model = _model()
    for payload in (model.mapping_entry(), model.public_row()):
        assert payload["is_local"] is True
        assert "actual_provider" not in payload
        assert "codex-local" not in payload.values()
