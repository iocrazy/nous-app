# backend/tests/test_mediahub_model_health_poll.py
"""Scheduled 6-hour platform-model health poll: probe every enabled Nous model
and persist the result (reusing the manual-Test probe + persistence)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_poll_probes_only_enabled_and_persists():
    """The step probes ENABLED models only and records each result; disabled
    rows are skipped, and the summary counts ok/failed."""
    from app.workflows.scheduled_health import probe_mediahub_models_step

    rows = [
        {"id": "1", "is_enabled": True, "type": "llm", "actual_model": "good"},
        {"id": "2", "is_enabled": True, "type": "embedding", "actual_model": "bad"},
        {"id": "3", "is_enabled": False, "type": "llm", "actual_model": "off"},
    ]
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    repo.record_test_result = AsyncMock(return_value={})

    async def _fake_probe(row):
        if row["actual_model"] == "good":
            return {"ok": True, "detail": "chat ok", "error": None, "dims": None}
        return {"ok": False, "detail": "", "error": "HTTP 401: bad key", "dims": None}

    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.mediahub_model_health.probe_mediahub_model",
            new=AsyncMock(side_effect=_fake_probe),
        ):
            summary = await probe_mediahub_models_step()

    assert summary == {"total": 2, "ok": 1, "failed": 1}
    # Disabled row (id=3) never probed/persisted.
    persisted = {c.args[0] for c in repo.record_test_result.await_args_list}
    assert persisted == {"1", "2"}
    repo.record_test_result.assert_any_await("1", "ok", "chat ok")
    repo.record_test_result.assert_any_await("2", "fail", "HTTP 401: bad key")


@pytest.mark.asyncio
async def test_poll_no_models_is_noop():
    """No enabled models → zero probes, empty summary, no crash."""
    from app.workflows.scheduled_health import probe_mediahub_models_step

    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[])
    repo.record_test_result = AsyncMock()

    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        summary = await probe_mediahub_models_step()

    assert summary == {"total": 0, "ok": 0, "failed": 0}
    repo.record_test_result.assert_not_awaited()
