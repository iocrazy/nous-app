"""``kind="budget"``: the three-way question a 100 % budget halt asks
(spec 2a §3). Singleton per run (``budget:<run>``).

``on_answer(target, value, ctx)`` runs in the reply endpoint BEFORE the wake
is delivered (``issue_messages_router._validate_typed_answer``); a rejection
is the endpoint's typed 4xx and nothing is woken. The endpoint may call it
twice (double-send window), so every branch is idempotent:

- **Top up** — re-read the issue's budget and spend NOW (not the row the
  endpoint loaded); still exhausted → 409 ``budget_still_exhausted``. A
  budget set to NULL counts as topped up (unlimited). Nothing else to do:
  the wake runs the next turn under the new budget.
- **Wrap up** — stamp ``execution_state.budget_wrap_up{run_id, at}`` (the
  loader consumes it for the NEXT run, once) and queue a steer that tells
  the agent to finish in one step. A repeat rewrites the same flag and
  queues a second identical steer (harmless: same words, claimed together).
- **Cancel** — ``transition_status(issue, "cancelled")`` +
  ``outcome_reason: budget_exhausted``; the woken workflow exits on
  ``PREEMPT_STATUSES``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.ai.runner.question import AnswerRejected, register_kind

BUDGET_OPTIONS: list[dict[str, Any]] = [
    {"label": "Top up", "description": "Raise the issue's budget, then continue."},
    {"label": "Wrap up", "description": "Let the agent finish in one more step."},
    {"label": "Cancel", "description": "Stop here and cancel the issue."},
]

WRAP_UP_STEER = (
    "Budget is exhausted. Finish in one step: summarize what is done and stop."
)


def _issue_id(target: dict) -> int:
    iid = (target or {}).get("id")
    if iid is None:
        raise AnswerRejected(409, "no_issue_target")
    return int(iid)


async def _top_up(issue_id: int) -> None:
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository

    fresh = await issue_repository.get_by_id(issue_id)
    budget = (fresh or {}).get("budget_cents")
    if not isinstance(budget, int) or budget < 0:
        return  # NULL = unlimited: topped up by removal
    spent = await get_agent_runs_repository().spent_cents_for_issue(
        issue_id=issue_id,
        conversation_id=_conversation_id(fresh),
    )
    if budget <= float(spent):
        raise AnswerRejected(409, "budget_still_exhausted")


def _conversation_id(row: dict | None) -> int | None:
    sid = (row or {}).get("ai_session_id")
    return int(sid) if sid is not None else None


async def _wrap_up(issue_id: int, ctx: Any) -> None:
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.services.issues.execution_state import merge_execution_state

    marker = getattr(ctx, "marker", None) or {}
    await merge_execution_state(
        issue_id,
        {
            "budget_wrap_up": {
                "run_id": marker.get("run_id"),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    await get_agent_run_inbox_repository().enqueue(
        target_kind="issue",
        target_id=issue_id,
        user_id=str(getattr(ctx, "user_id", "")),
        kind="steer",
        content={"body": WRAP_UP_STEER, "attachments": []},
    )


async def _cancel(issue_id: int) -> None:
    from app.repositories.issue_repository import issue_repository
    from app.services.issues.execution_state import merge_execution_state

    await issue_repository.transition_status(issue_id, "cancelled")
    await merge_execution_state(issue_id, {"outcome_reason": "budget_exhausted"})


async def on_answer(target: dict, value: str, ctx: Any) -> None:
    issue_id = _issue_id(target)
    if value == "Top up":
        await _top_up(issue_id)
    elif value == "Wrap up":
        await _wrap_up(issue_id, ctx)
    elif value == "Cancel":
        await _cancel(issue_id)
    else:
        raise AnswerRejected(400, "answer_shape")


register_kind("budget", on_answer, singleton=True)

__all__ = ["BUDGET_OPTIONS", "WRAP_UP_STEER", "on_answer"]
