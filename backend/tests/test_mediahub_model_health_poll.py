# backend/tests/test_mediahub_model_health_poll.py
"""Scheduled hourly platform-model health poll: probe every enabled Nous model
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
            return {
                "ok": True,
                "detail": "chat ok",
                "error": None,
                "dims": None,
                "code": None,
            }
        return {
            "ok": False,
            "detail": "",
            "error": "HTTP 401: bad key",
            "dims": None,
            "code": "auth",
        }

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
    # The hourly poll is the ONLY writer for most rows (nobody clicks Test), so
    # if the reason code did not travel this call it would be NULL in production
    # forever — the acceptance query would come back all-empty.
    repo.record_test_result.assert_any_await("1", "ok", "chat ok", None)
    repo.record_test_result.assert_any_await("2", "fail", "HTTP 401: bad key", "auth")


@pytest.mark.asyncio
async def test_poll_logs_one_warning_per_failed_model():
    """F1 hole two: the poll used to log only the ``5/11 unreachable`` summary,
    so when ``last_test_detail`` came back empty there was no second place to
    recover the reason from. Each failure now names the model and its reason;
    healthy models stay quiet."""
    from app.workflows.scheduled_health import probe_mediahub_models_step

    rows = [
        {
            "id": "1",
            "name": "mediahub-deepseek-v4-pro",
            "is_enabled": True,
            "type": "llm",
            "actual_model": "good",
        },
        {
            "id": "2",
            "name": "mediahub-deepseek-v4-flash",
            "is_enabled": True,
            "type": "llm",
            "actual_model": "bad",
        },
    ]
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    repo.record_test_result = AsyncMock(return_value={})

    async def _fake_probe(row):
        if row["actual_model"] == "good":
            return {"ok": True, "detail": "chat ok", "error": None, "dims": None}
        return {
            "ok": False,
            "detail": "",
            "error": "ReadTimeout: <no message>",
            "dims": None,
        }

    logger = MagicMock()
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.mediahub_model_health.probe_mediahub_model",
            new=AsyncMock(side_effect=_fake_probe),
        ):
            with patch("app.workflows.scheduled_health.logger", logger):
                await probe_mediahub_models_step()

    assert logger.warning.call_count == 1
    message = logger.warning.call_args[0][0]
    assert "mediahub-deepseek-v4-flash" in message
    assert "ReadTimeout" in message
    assert "mediahub-deepseek-v4-pro" not in message


@pytest.mark.asyncio
async def test_poll_warning_never_blank_when_reason_missing():
    """Defence in depth: even if a probe somehow reports no reason at all, the
    log line must not trail off into nothing — a blank reason is exactly the
    signal that misled the 2026-08-14 diagnosis."""
    from app.workflows.scheduled_health import probe_mediahub_models_step

    repo = MagicMock()
    repo.list_all = AsyncMock(
        return_value=[{"id": "9", "name": "mediahub-mystery", "is_enabled": True}]
    )
    repo.record_test_result = AsyncMock(return_value={})

    logger = MagicMock()
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.mediahub_model_health.probe_mediahub_model",
            new=AsyncMock(
                return_value={"ok": False, "detail": "", "error": "", "dims": None}
            ),
        ):
            with patch("app.workflows.scheduled_health.logger", logger):
                await probe_mediahub_models_step()

    message = logger.warning.call_args[0][0]
    assert "mediahub-mystery" in message
    assert "<no detail>" in message


def test_poll_runs_hourly():
    """F3: 6h between probes meant a red light could be five hours stale before
    anyone saw it. The probe is one ``max_tokens=8`` ping per model.

    The cron is matched against THIS workflow specifically — a bare
    ``"0 * * * *" in source`` would already pass on ``health_check_workflow``'s
    schedule and never fail if this one stayed at 6h."""
    import inspect
    import re

    from app.workflows import scheduled_health

    source = inspect.getsource(scheduled_health)
    match = re.search(
        r'@DBOS\.scheduled\("([^"]+)"\)[^@]*@DBOS\.workflow\(\)\s*'
        r"async def mediahub_model_health_workflow",
        source,
    )
    assert match, "could not locate the platform-model health schedule"
    assert match.group(1) == "0 * * * *"


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
