"""The hourly nous-model probe step first mirrors nous-engine into the catalog.

Sync runs INSIDE ``probe_nous_models_step`` (the workflow body is untouched —
no new step), before the probe loop, so a service the engine just started
serving gets a row and is probed in the same run. Sync is best-effort: its
failure is logged and the probes still run.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.nous_engine_sync import SyncReport

_OK = {"ok": True, "detail": "loaded", "error": None, "dims": None, "code": None}
_REPO_PATH = "app.repositories.nous_model_repository.get_nous_model_repository"
_SYNC_PATH = "app.services.ai.nous_engine_sync.sync_all_engines"
_REFRESH_PATH = "app.agent_framework.catalog_windows.refresh_catalog_windows"
_PROBE_PATH = "app.services.ai.nous_model_health.probe_nous_model"


def _engine_row(**over: Any) -> dict[str, Any]:
    row = {
        "id": 1900000000000000001,
        "name": "nous-qwen3-8-27b",
        "is_enabled": True,
        "type": "llm",
        "actual_provider": "nous",
        "actual_model": "qwen3-8-27b",
        "api_key": "sk-plain",
        "base_url": "http://host.docker.internal:8000/v1",
        "owner_user_id": None,
        "sort_order": 0,
    }
    row.update(over)
    return row


def _repo(first: list[dict], second: list[dict]) -> MagicMock:
    repo = MagicMock()
    repo.list_all = AsyncMock(side_effect=[first, second])
    repo.record_test_result = AsyncMock(return_value={})
    return repo


@pytest.mark.asyncio
async def test_sync_runs_before_probes_and_new_rows_are_probed_same_run():
    from app.workflows.scheduled_health import probe_nous_models_step

    base = _engine_row()
    new = _engine_row(
        id=1900000000000000002,
        name="nous-moss-asr",
        actual_model="moss-asr",
        type="asr",
    )
    repo = _repo([base], [base, new])
    report = SyncReport(discovered=2, created=("nous-moss-asr",))
    sync = AsyncMock(return_value=[("http://host.docker.internal:8000/v1", report)])
    refresh = AsyncMock()

    with (
        patch(_REPO_PATH, return_value=repo),
        patch(_SYNC_PATH, sync),
        patch(_REFRESH_PATH, refresh),
        patch(_PROBE_PATH, new=AsyncMock(return_value=_OK)),
    ):
        summary = await probe_nous_models_step()

    sync.assert_awaited_once()
    assert sync.await_args.args[0] == [base]  # endpoints from the pre-sync read
    refresh.assert_awaited_once()
    assert summary["total"] == 2  # the synced row was probed in this run
    persisted = {c.args[0] for c in repo.record_test_result.await_args_list}
    assert persisted == {"1900000000000000001", "1900000000000000002"}


@pytest.mark.asyncio
async def test_sync_crash_does_not_stop_probes():
    from app.workflows.scheduled_health import probe_nous_models_step

    base = _engine_row()
    repo = _repo([base], [base])
    probe = AsyncMock(return_value=_OK)

    with (
        patch(_REPO_PATH, return_value=repo),
        patch(_SYNC_PATH, AsyncMock(side_effect=RuntimeError("engine sync exploded"))),
        patch(_PROBE_PATH, new=probe),
    ):
        summary = await probe_nous_models_step()

    assert summary == {"total": 1, "ok": 1, "failed": 0, "idle": 0, "not_probed": 0}
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_nothing_changed_skips_the_window_reload():
    from app.workflows.scheduled_health import probe_nous_models_step

    base = _engine_row()
    repo = _repo([base], [base])
    sync = AsyncMock(return_value=[("x", SyncReport(discovered=1))])
    refresh = AsyncMock()

    with (
        patch(_REPO_PATH, return_value=repo),
        patch(_SYNC_PATH, sync),
        patch(_REFRESH_PATH, refresh),
        patch(_PROBE_PATH, new=AsyncMock(return_value=_OK)),
    ):
        await probe_nous_models_step()

    refresh.assert_not_awaited()


def test_workflow_body_still_calls_only_the_one_step():
    """Guard: sync lives inside the step, not as a new step in the body."""
    from app.workflows import scheduled_health

    body = inspect.getsource(
        inspect.unwrap(scheduled_health.nous_model_health_workflow)
    )
    assert "engine_sync" not in body and "sync_all_engines" not in body
    assert body.count("await ") == 1


def test_sync_helper_is_not_a_step():
    from app.workflows.scheduled_health import _sync_engine_catalog

    assert not hasattr(_sync_engine_catalog, "dbos_function_name")
    assert inspect.unwrap(_sync_engine_catalog) is _sync_engine_catalog
