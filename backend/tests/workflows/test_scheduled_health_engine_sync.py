"""No scheduled job writes nous-engine state into the catalog (spec 2026-09-25 §3.4).

The every-minute ``nous_engine_sync_workflow`` and the sync the hourly probe
step used to run first are gone: whether a service is authorized and loaded
is read live by the platform view, and new engine rows are created only by
the admin "Sync from nous-engine" button. These tests pin the removal and
that the hourly workflow body still calls exactly one step.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_PATH = "app.repositories.nous_model_repository.get_nous_model_repository"
_SYNC_PATH = "app.services.ai.nous_engine_sync.sync_all_engines"
_OK = {"ok": True, "detail": "chat ok", "error": None, "dims": None, "code": None}


def test_workflow_body_still_calls_only_the_one_step():
    from app.workflows import scheduled_health

    body = inspect.getsource(
        inspect.unwrap(scheduled_health.nous_model_health_workflow)
    )
    assert body.count("await ") == 1
    assert "probe_nous_models_step()" in body


def test_the_minute_sync_workflow_and_step_are_gone():
    import app.workflows._scheduled_bundle as bundle
    from app.workflows import scheduled_health

    for name in (
        "nous_engine_sync_workflow",
        "sync_engine_catalog_step",
        "_sync_engine_catalog",
    ):
        assert not hasattr(scheduled_health, name), name
        assert not hasattr(bundle, name), name
    src = inspect.getsource(scheduled_health)
    assert "sync_all_engines" not in src
    assert '@DBOS.scheduled("* * * * *")' not in src


@pytest.mark.asyncio
async def test_probe_step_reads_the_catalog_once_and_never_syncs():
    from app.workflows.scheduled_health import probe_nous_models_step

    repo = MagicMock()
    repo.list_all = AsyncMock(
        return_value=[
            {
                "id": 1900000000000000001,
                "name": "deepseek-v4",
                "is_enabled": True,
                "type": "llm",
                "actual_provider": "deepseek",
            }
        ]
    )
    repo.record_test_result = AsyncMock(return_value={})
    sync = AsyncMock()
    with (
        patch(_REPO_PATH, return_value=repo),
        patch(_SYNC_PATH, sync),
        patch(
            "app.services.ai.nous_model_health.probe_nous_model",
            new=AsyncMock(return_value=_OK),
        ),
    ):
        summary = await probe_nous_models_step()

    sync.assert_not_awaited()
    repo.list_all.assert_awaited_once()
    assert summary == {
        "total": 1,
        "ok": 1,
        "failed": 0,
        "idle": 0,
        "not_probed": 0,
        "engine_live": 0,
    }
