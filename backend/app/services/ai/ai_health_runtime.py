"""Runtime health for AI capabilities — the layer the static board can't see.

A capability can resolve to a valid model+key yet still fail at call time
(the ark-key visual-analysis outage: key authenticated, the model endpoint
returned AccessDenied). This module reads recent terminal runs from
task_tracking (the UI's single source of truth — never query
dbos.workflow_status) and reports a per-task_type runtime summary so a
*currently failing* capability is flagged even when its config looks healthy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Recent window + row cap. AI task volume is low (single digits/week in
# prod), so a 7-day / 50-row window is plenty and stays bounded.
_WINDOW_DAYS = 7
_ROW_CAP = 50
_MAX_ERR_LEN = 200

_TERMINAL = ("completed", "failed")


def summarize_runtime(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse recent terminal task_tracking rows into a per-task_type
    runtime summary. Input MUST be newest-first and is expected to be
    pre-filtered to terminal rows; out-of-order or non-terminal rows are
    tolerated (non-terminal/typeless rows are skipped).

    Each summary: ``{recent_runs, recent_failures, last_error, latest_failed}``.
    ``latest_failed`` (most recent terminal run failed) is the "currently
    broken" signal — it distinguishes an ongoing outage from a healed blip.
    """
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:  # newest-first
        ttype = row.get("task_type")
        status = row.get("status")
        if not ttype or status not in _TERMINAL:
            continue
        stat = summary.get(ttype)
        if stat is None:
            # First row seen for this type == most recent (newest-first contract).
            stat = {
                "recent_runs": 0,
                "recent_failures": 0,
                "last_error": "",
                "latest_failed": status == "failed",
            }
            summary[ttype] = stat
        stat["recent_runs"] += 1
        if status == "failed":
            stat["recent_failures"] += 1
            if not stat["last_error"]:
                stat["last_error"] = (row.get("error_msg") or "").strip()[:_MAX_ERR_LEN]
    return summary


async def fetch_runtime_summary(
    user_id: str, task_types: list[str]
) -> dict[str, dict[str, Any]]:
    """Per-task_type runtime summary for the user's recent terminal runs.

    Never raises — any failure yields an empty summary so the static board
    is unaffected (the runtime layer is purely additive context).
    """
    if not user_id or not task_types:
        return {}
    try:
        from app.services.infra.unified_task_manager import get_task_manager

        since_iso = (
            datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
        ).isoformat()
        rows = await get_task_manager().get_recent_terminal_runs(
            user_id, task_types, since_iso=since_iso, cap=_ROW_CAP
        )
        return summarize_runtime(rows)
    except Exception:  # noqa: BLE001 — runtime layer must never sink the board
        logger.exception("[ai_health] runtime summary fetch failed")
        return {}


__all__ = ["summarize_runtime", "fetch_runtime_summary"]
