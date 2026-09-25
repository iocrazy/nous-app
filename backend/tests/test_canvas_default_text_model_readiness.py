""" "Catalog default" text model must skip rows the probe says cannot answer.

An empty ``provider_slug`` on a canvas text Run resolves to a catalog row
(``CanvasRunService._default_text_model``). It used to take the first enabled
``llm`` row blindly, so a default could land on a local nous-engine row that is
``idle`` (authorized, not loaded — on 2026-09-24 a real chat to such a row got
503 "not loaded") or on a ``fail`` row, while the pickers grey/hide exactly
those rows. Ranking: ``ok`` first, then never-probed / ``not_probed``; ``idle``
and ``fail`` are skipped. If nothing qualifies, today's first-row behaviour is
kept — but loudly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

from app.services.canvas.canvas_run_service import CanvasRunService


def _row(name: str, status: str | None, sort_order: int) -> dict:
    # Shape of list_enabled("llm"): the public projection, sort_order ascending.
    return {
        "id": 7000000000000000000 + sort_order,
        "name": name,
        "display_name": name,
        "actual_model": name,
        "type": "llm",
        "pricing_type": "per_token",
        "pricing_value": None,
        "sort_order": sort_order,
        "last_test_status": status,
        "last_tested_at": None,
        "last_test_code": None,
        "is_local": status == "idle",
    }


async def _default(rows: list[dict]) -> tuple[str, str]:
    repo = MagicMock()
    repo.list_enabled = AsyncMock(return_value=rows)
    records: list = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        with (
            patch(
                "app.repositories.nous_model_repository.get_nous_model_repository",
                return_value=repo,
            ),
            patch(
                "app.services.ai.providers.ai_provider_helpers.get_maintenance_model",
                new=AsyncMock(return_value="maintenance-model"),
            ),
        ):
            model = await CanvasRunService()._default_text_model()
    finally:
        logger.remove(sink_id)
    return model, " ".join(str(r) for r in records)


@pytest.mark.asyncio
async def test_ok_row_wins_over_earlier_idle_fail_and_unprobed_rows():
    model, _ = await _default(
        [
            _row("nous-idle", "idle", 0),
            _row("broken", "fail", 1),
            _row("unprobed", None, 2),
            _row("healthy", "ok", 3),
        ]
    )
    assert model == "healthy"


@pytest.mark.parametrize("status", [None, "not_probed"])
@pytest.mark.asyncio
async def test_unprobed_row_is_the_fallback_when_no_row_is_ok(status):
    model, warnings = await _default(
        [
            _row("nous-idle", "idle", 0),
            _row("broken", "fail", 1),
            _row("unprobed", status, 2),
        ]
    )
    assert model == "unprobed"
    assert warnings == ""


@pytest.mark.asyncio
async def test_all_idle_or_fail_keeps_first_row_and_warns():
    model, warnings = await _default(
        [_row("nous-idle", "idle", 0), _row("broken", "fail", 1)]
    )
    assert model == "nous-idle"
    assert "idle" in warnings or "fail" in warnings, warnings


@pytest.mark.asyncio
async def test_empty_catalog_still_falls_back_to_maintenance_model():
    model, _ = await _default([])
    assert model == "maintenance-model"
