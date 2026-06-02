"""Pure pivot for the per-task_type queue breakdown (ops observability):
running/pending counts + oldest queued age, from task_tracking rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.infra.system_monitor_service import _aggregate_breakdown

NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _row(task_type, phase, ago_sec):
    return {
        "task_type": task_type,
        "phase": phase,
        "created_at": NOW - timedelta(seconds=ago_sec),
    }


def test_counts_running_and_pending_per_type():
    rows = [
        _row("download", "processing", 5),
        _row("download", "queued", 30),
        _row("download", "queued", 90),
        _row("ai_summary", "processing", 2),
    ]
    out = {r["task_type"]: r for r in _aggregate_breakdown(rows, NOW)}
    assert out["download"]["running"] == 1
    assert out["download"]["pending"] == 2
    assert out["ai_summary"]["running"] == 1
    assert out["ai_summary"]["pending"] == 0


def test_oldest_queued_age_is_max_wait():
    rows = [_row("download", "queued", 30), _row("download", "queued", 95)]
    out = {r["task_type"]: r for r in _aggregate_breakdown(rows, NOW)}
    # Oldest queued = the one that has waited longest (95s ago).
    assert out["download"]["oldest_queued_age_sec"] == 95


def test_no_queued_means_zero_age():
    rows = [_row("download", "processing", 5)]
    out = {r["task_type"]: r for r in _aggregate_breakdown(rows, NOW)}
    assert out["download"]["oldest_queued_age_sec"] == 0


def test_empty_rows():
    assert _aggregate_breakdown([], NOW) == []


def test_iso_string_created_at_is_parsed():
    rows = [
        {
            "task_type": "parse",
            "phase": "queued",
            "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        }
    ]
    out = {r["task_type"]: r for r in _aggregate_breakdown(rows, NOW)}
    assert out["parse"]["pending"] == 1
    assert out["parse"]["oldest_queued_age_sec"] == 10
