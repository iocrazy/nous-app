"""Runtime layer for the AI capability health board.

A capability can resolve to a valid model+key yet still fail at call time
(the ark-key visual-analysis outage: key authenticated, model endpoint
returned AccessDenied). ``summarize_runtime`` collapses recent terminal
task_tracking rows into a per-task_type signal so a *currently failing*
capability is flagged even when its static config looks healthy.
"""

from __future__ import annotations

import pytest

from app.services.ai import ai_health_runtime as rt


def _row(task_type, status, error_msg="", created_at="2026-06-13T00:00:00Z"):
    return {
        "task_type": task_type,
        "status": status,
        "error_msg": error_msg,
        "created_at": created_at,
    }


def test_empty_input_yields_empty_summary():
    assert rt.summarize_runtime([]) == {}


def test_counts_runs_and_failures_per_type():
    rows = [
        _row("ai_extract", "completed"),
        _row("ai_extract", "failed", "AccessDenied"),
        _row("ai_transcription", "completed"),
    ]
    out = rt.summarize_runtime(rows)
    assert out["ai_extract"]["recent_runs"] == 2
    assert out["ai_extract"]["recent_failures"] == 1
    assert out["ai_transcription"]["recent_runs"] == 1
    assert out["ai_transcription"]["recent_failures"] == 0


def test_latest_failed_true_when_most_recent_run_failed():
    # newest-first contract: the first row is the most recent run.
    rows = [
        _row("ai_extract", "failed", "AccessDenied: model not granted"),
        _row("ai_extract", "completed"),
    ]
    out = rt.summarize_runtime(rows)
    assert out["ai_extract"]["latest_failed"] is True
    assert "AccessDenied" in out["ai_extract"]["last_error"]


def test_latest_failed_false_when_most_recent_run_succeeded():
    # A healed capability: an old failure but the latest run completed.
    rows = [
        _row("ai_extract", "completed"),
        _row("ai_extract", "failed", "transient blip"),
    ]
    out = rt.summarize_runtime(rows)
    assert out["ai_extract"]["latest_failed"] is False
    # last_error still captured for context even though it healed.
    assert out["ai_extract"]["recent_failures"] == 1


def test_last_error_is_most_recent_failure():
    rows = [
        _row("ai_extract", "failed", "newest error"),
        _row("ai_extract", "failed", "older error"),
    ]
    out = rt.summarize_runtime(rows)
    assert out["ai_extract"]["last_error"] == "newest error"


def test_non_terminal_and_typeless_rows_ignored():
    rows = [
        _row("ai_extract", "in_progress"),
        _row("ai_extract", "queued"),
        _row(None, "failed", "no type"),
        _row("ai_extract", "completed"),
    ]
    out = rt.summarize_runtime(rows)
    assert out["ai_extract"]["recent_runs"] == 1
    assert out["ai_extract"]["latest_failed"] is False


def test_error_message_truncated():
    long_err = "x" * 500
    out = rt.summarize_runtime([_row("ai_extract", "failed", long_err)])
    assert len(out["ai_extract"]["last_error"]) <= rt._MAX_ERR_LEN


@pytest.mark.asyncio
async def test_fetch_runtime_summary_empty_args_short_circuits():
    assert await rt.fetch_runtime_summary("", ["ai_extract"]) == {}
    assert await rt.fetch_runtime_summary("u1", []) == {}


@pytest.mark.asyncio
async def test_fetch_runtime_summary_swallows_errors(monkeypatch):
    # A broken manager must not sink the board — empty summary, no raise.
    class _BoomManager:
        async def get_recent_terminal_runs(self, *a, **k):
            raise RuntimeError("db down")

    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(utm, "get_task_manager", lambda: _BoomManager())
    assert await rt.fetch_runtime_summary("u1", ["ai_extract"]) == {}
