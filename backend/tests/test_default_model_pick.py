"""Shared ranking for "which enabled llm row is the implicit default".

Used by the canvas Catalog default and the scorer health resolver. ``ok``
first, then never-probed / ``not_probed`` (catalog order within a rank);
``idle`` (nous-engine: authorized, not loaded — a real chat got 503 on
2026-09-24) and ``fail`` are skipped. Nothing qualifying → the rows unchanged,
with a WARN, so callers keep their old first-row behaviour loudly.
"""

from __future__ import annotations

from loguru import logger

from app.services.ai.default_model_pick import rank_default_candidates


def _row(name: str, status: str | None) -> dict:
    return {"name": name, "last_test_status": status}


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


def _capture(rows: list[dict]) -> tuple[list[dict], str]:
    records: list = []
    sink = logger.add(records.append, level="WARNING")
    try:
        out = rank_default_candidates(rows, context="test")
    finally:
        logger.remove(sink)
    return out, " ".join(str(r) for r in records)


def test_ok_then_unprobed_in_catalog_order_idle_and_fail_dropped():
    out, warnings = _capture(
        [
            _row("idle", "idle"),
            _row("unprobed-a", None),
            _row("broken", "fail"),
            _row("ok-a", "ok"),
            _row("unprobed-b", "not_probed"),
            _row("ok-b", "ok"),
        ]
    )
    assert _names(out) == ["ok-a", "ok-b", "unprobed-a", "unprobed-b"]
    assert warnings == ""


def test_nothing_qualifies_returns_rows_unchanged_and_warns():
    rows = [_row("idle", "idle"), _row("broken", "fail")]
    out, warnings = _capture(rows)
    assert _names(out) == ["idle", "broken"]
    assert "test" in warnings and "idle" in warnings


def test_empty_is_empty_without_warning():
    out, warnings = _capture([])
    assert out == [] and warnings == ""
