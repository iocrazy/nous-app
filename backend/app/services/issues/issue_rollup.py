"""``issue.rollup`` — one issue's progress across all its runs (spec §1-② view
family, read-time). Pure ``compute_rollup`` + an async ``load_rollup`` that
gathers its inputs; the endpoint is ``GET /issues/{id}/progress``.

Phase is derived from the RUNS, not from ``issues.execution_state`` — the
MH-1 drift ("running · turn 7 · 2474h" with no running run) is exactly what
trusting the decoration produced. Priority:
``paused > waiting_input > running > blocked > done > idle``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from app.services.issues.origin_resolvers import resolve_origin

TERMINAL_ISSUE = frozenset({"done", "cancelled", "closed"})
FAILED_RUN = frozenset({"failed", "heartbeat_lost"})


def _view(run: dict[str, Any]) -> dict[str, Any]:
    meta = run.get("metadata_json") or {}
    return meta.get("view") or {} if isinstance(meta, dict) else {}


def _cost(run: dict[str, Any]) -> dict[str, Any]:
    meta = run.get("metadata_json") or {}
    return meta.get("cost") or {} if isinstance(meta, dict) else {}


def _run_cents(run: dict[str, Any]) -> float:
    if run.get("status") == "running":
        return float(_cost(run).get("spent_cents") or 0.0)
    try:
        return float(run.get("cost_cents") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def derive_phase(issue: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    state = issue.get("execution_state") or {}
    if not isinstance(state, dict):
        state = {}
    current = next((r for r in runs if r.get("status") == "running"), None)
    latest = runs[0] if runs else None
    if issue.get("paused_at"):
        return "paused"
    if (
        "awaiting_input" in state
        or state.get("agent_outcome") == "needs_input"
        or (current is not None and _view(current).get("phase") == "waiting_input")
        or (
            latest is not None
            and (_view(latest).get("ended") or {}).get("reason") == "awaiting_approval"
        )
    ):
        return "waiting_input"
    if current is not None:
        return "running"
    if issue.get("status") in TERMINAL_ISSUE:
        return "done"
    if issue.get("status") == "blocked" or (
        latest is not None and latest.get("status") in FAILED_RUN
    ):
        return "blocked"
    return "idle"


def compute_rollup(
    issue: dict[str, Any],
    runs: list[dict[str, Any]],
    sub_issues: list[dict[str, Any]],
    inbox_pending: int,
    origin: dict[str, Any],
    *,
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """``runs`` newest first, root runs only."""
    now = now or dt.datetime.now(dt.timezone.utc)
    current = next((r for r in runs if r.get("status") == "running"), None)
    spent = round(sum(_run_cents(r) for r in runs), 4)
    budget = issue.get("budget_cents")
    # Same reading as BudgetGateHook: NULL = unlimited (no pct), 0 = a real
    # budget meaning "spend nothing more" — any spend is 100 % over it. Before
    # 2026-09-06 the `budget > 0` guard left 0 looking unlimited (pct None,
    # state ok) while the hook on the same run had already recorded
    # budget_check{halt, pct 100}: two answers for one number.
    pct: Optional[int] = None
    if isinstance(budget, int) and budget >= 0:
        if budget > 0:
            pct = round(spent * 100 / budget)
        else:
            pct = 100 if spent > 0 else 0
    state = "ok"
    if pct is not None:
        state = "over" if pct >= 100 else "warn" if pct >= 80 else "ok"
    done_children = sum(1 for s in sub_issues if s.get("status") in TERMINAL_ISSUE)
    return {
        "issue_id": str(issue["id"]),
        "status": issue.get("status"),
        "phase": derive_phase(issue, runs),
        "paused_at": issue.get("paused_at"),
        "current_run": (
            {
                "id": str(current["id"]),
                "status": current.get("status"),
                "started_at": current.get("started_at"),
                "model": current.get("model"),
                "view": _view(current),
                "cost": _cost(current),
            }
            if current
            else None
        ),
        "runs": [
            {
                "id": str(r["id"]),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
                "ended_at": r.get("ended_at"),
                "model": r.get("model"),
                "error_code": r.get("error_code"),
                "cost_cents": _run_cents(r),
                "ended": _view(r).get("ended"),
                "step": _view(r).get("step"),
            }
            for r in runs
        ],
        "sub_issues": {
            "total": len(sub_issues),
            "done": done_children,
            "items": [
                {
                    "id": str(s["id"]),
                    "identifier": s.get("identifier"),
                    "title": s.get("title"),
                    "status": s.get("status"),
                }
                for s in sub_issues
            ],
        },
        "inbox_pending": int(inbox_pending),
        "budget": {
            "budget_cents": budget if isinstance(budget, int) else None,
            "spent_cents": spent,
            "pct": pct,
            "state": state,
        },
        "origin": origin,
        "execution_state": state_dict(issue),
        "computed_at": now,
    }


def state_dict(issue: dict[str, Any]) -> dict[str, Any]:
    s = issue.get("execution_state")
    return s if isinstance(s, dict) else {}


async def load_rollup(issue: dict[str, Any]) -> dict[str, Any]:
    """Gather runs / children / inbox / origin and fold them."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository

    issue_id = int(issue["id"])
    session_id = issue.get("ai_session_id")
    runs = await get_agent_runs_repository().list_for_issue(
        issue_id=issue_id,
        conversation_id=int(session_id) if session_id else None,
    )
    children = await issue_repository.list_children(issue_id)
    pending = await get_agent_run_inbox_repository().pending_count(
        target_kind="issue", target_id=issue_id
    )
    origin = await resolve_origin(issue)
    return compute_rollup(issue, runs, children, pending, origin)


__all__ = ["compute_rollup", "derive_phase", "load_rollup"]
