""" "Catalog default" text model follows the platform view's live status.

An empty ``provider_slug`` on a canvas text Run resolves to a catalog row
(``CanvasRunService._default_text_model``). It used to take the first enabled
``llm`` row blindly, so a default could land on a local nous-engine row that is
``idle`` (authorized, not loaded — on 2026-09-24 a real chat to such a row got
503 "not loaded") while a loaded row sat further down. Rows now come from
``platform_rows_with_status`` (live engine state; ``fail`` rows and services
the engine no longer lists are already gone). Ranking: ``ok`` first, then
``not_probed``, then ``idle``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from loguru import logger

from app.services.canvas.canvas_run_service import CanvasRunService


def _row(name: str, status: str, sort_order: int) -> dict:
    # Shape of platform_rows_with_status("llm"): public projection + status.
    return {
        "id": 7000000000000000000 + sort_order,
        "name": name,
        "display_name": name,
        "actual_model": name,
        "type": "llm",
        "pricing_type": "per_token",
        "pricing_value": 0.0,
        "sort_order": sort_order,
        "last_test_status": status,
        "last_tested_at": None,
        "last_test_code": None,
        "is_local": False,
        "status": status,
    }


async def _default(rows: list[dict]) -> tuple[str, str, AsyncMock]:
    live = AsyncMock(return_value=rows)
    records: list = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        with (
            patch("app.services.ai.platform_provider.platform_rows_with_status", live),
            patch(
                "app.services.ai.providers.ai_provider_helpers.get_maintenance_model",
                new=AsyncMock(return_value="maintenance-model"),
            ),
        ):
            model = await CanvasRunService()._default_text_model()
    finally:
        logger.remove(sink_id)
    return model, " ".join(str(r) for r in records), live


@pytest.mark.asyncio
async def test_ok_row_wins_over_earlier_idle_and_unprobed_rows():
    model, _, live = await _default(
        [
            _row("nous-idle", "idle", 0),
            _row("unprobed", "not_probed", 2),
            _row("healthy", "ok", 3),
        ]
    )
    assert model == "healthy"
    live.assert_awaited_once_with("llm")


@pytest.mark.asyncio
async def test_not_probed_row_beats_idle_when_no_row_is_ok():
    model, warnings = (
        await _default(
            [_row("nous-idle", "idle", 0), _row("unprobed", "not_probed", 2)]
        )
    )[:2]
    assert model == "unprobed"
    assert warnings == ""


@pytest.mark.asyncio
async def test_idle_row_is_still_a_default_when_it_is_all_there_is():
    model, warnings = (await _default([_row("nous-idle", "idle", 0)]))[:2]
    assert model == "nous-idle"
    assert warnings == ""


@pytest.mark.asyncio
async def test_empty_catalog_still_falls_back_to_maintenance_model():
    model, _, _ = await _default([])
    assert model == "maintenance-model"
