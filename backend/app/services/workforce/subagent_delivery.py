"""Deliver one background sub-agent's result to its parent — once (fh4 E2).

Two producers write a background child's ``subagent_result``: the worker's own
completion (``agent_worker._run_subagent_task``) and the stale-task reaper
closing a child whose worker is gone (``stale_tasks._fail_worker_lost``). Both
go through :func:`deliver_subagent_result`, so they share one dedupe key and
one reply-target reading, and the first writer's row is what both report.

Where this runs
===============
Both callers run inside a DBOS STEP (``run_one_task_step`` /
``reap_stale_workforce_tasks_step``). Nothing here dispatches: the wake-up is
returned as an ORDER (``idle_dispatch``) that the workflow body carries out,
because DBOS refuses ``start_workflow`` from inside a step. Nothing here is a
step either — these are plain async reads and writes, idempotent by the
dedupe key, which is what makes them safe to re-run on a replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.services.workforce.settle import (
    SettleReason,
    settle_reason_for_envelope,
    settle_reason_for_lost_run,
    subagent_result_dedupe_key,
)

#: Run statuses a replayed step may deliver from. ``running`` is not a result.
_RUN_STATUS_TO_ENVELOPE = {
    "completed": "success",
    "cancelled": "cancelled",
}


@dataclass(frozen=True)
class DeliveryOutcome:
    """What one delivery attempt did.

    ``stored`` is the live inbox row under the task's dedupe key — ours, or the
    one an earlier writer filed first. ``failure`` is a typed task error code
    (``result_delivery_failed`` / ``no_reply_target``) or None. ``idle_dispatch``
    is the wake ORDER for the workflow body, None for a conversation target
    or a failed delivery.
    """

    stored: Optional[dict[str, Any]]
    failure: Optional[str]
    idle_dispatch: Optional[dict[str, Any]]


def _reply_target(payload: dict[str, Any]) -> tuple[str, Optional[int]]:
    """``(target_kind, target_id)`` from ``payload.reply_to``; ``("", None)``
    for anything malformed — a bad reply target must never raise here, the
    child has already run."""
    reply_to = payload.get("reply_to") or {}
    target_kind = str(reply_to.get("target_kind") or "")
    try:
        target_id = int(reply_to["target_id"]) if target_kind else None
    except (KeyError, TypeError, ValueError):
        return "", None
    return target_kind, target_id


async def deliver_subagent_result(
    *,
    inbox_repo: Any,
    task_id: str,
    payload: dict[str, Any],
    content: dict[str, Any],
) -> DeliveryOutcome:
    """File ``content`` as the task's one ``subagent_result`` and return what
    happened. Never raises: every failure is logged and typed."""
    target_kind, target_id = _reply_target(payload)
    if not target_kind or target_id is None:
        logger.error(
            f"[subagent-delivery] task {task_id} has no usable reply target "
            f"({payload.get('reply_to')!r}); the result has nowhere to go"
        )
        return DeliveryOutcome(
            stored=None, failure="no_reply_target", idle_dispatch=None
        )
    try:
        stored = await inbox_repo.enqueue(
            target_kind=target_kind,
            target_id=target_id,
            user_id=str(payload["user_id"]),
            kind="subagent_result",
            content=content,
            dedupe_key=subagent_result_dedupe_key(task_id),
        )
    except Exception as err:  # noqa: BLE001 — typed failure, never raised
        logger.exception(
            f"[subagent-delivery] task {task_id}: the result could not be "
            f"delivered to {target_kind} {target_id}: {err}"
        )
        return DeliveryOutcome(
            stored=None, failure="result_delivery_failed", idle_dispatch=None
        )
    # The wake ORDER, not the wake. Only an issue has turns to start — and
    # only while the result is still unread. When dedupe hands back a row that
    # is already CLAIMED (the worker filed it, the drain's turn read it, and
    # now the reaper or a replayed step arrives), a turn has consumed it:
    # ordering another wake-up would start a billed "Continue" on an empty
    # inbox under a workflow id the drain's turn does not share (fh4 review M1).
    unread = not (stored or {}).get("claimed_at")
    idle = (
        {"issue_id": int(target_id), "user_id": str(payload["user_id"])}
        if target_kind == "issue" and unread
        else None
    )
    return DeliveryOutcome(stored=stored, failure=None, idle_dispatch=idle)


async def terminal_run_for_task(workforce: Any, task_id: Any) -> Optional[dict]:
    """The task's run row when it has already ENDED, else None.

    The replay guard. ``run_one_task_step`` re-runs from the top when DBOS
    recovers it after a crash, and ``claim_task`` re-admits the same workflow
    id; without this check the child runs — and bills — a second time. A read
    error answers None (run the child, today's behaviour) and says so: a
    duplicate run costs money, a result never delivered costs the answer."""
    try:
        run = await workforce.latest_run_for_task(str(task_id))
    except Exception as err:  # noqa: BLE001
        logger.error(
            f"[subagent-delivery] task {task_id}: could not check for an earlier "
            f"run ({err}); running the child"
        )
        return None
    if not run or run.get("status") in (None, "running"):
        return None
    return run


def envelope_from_prior_run(run: dict[str, Any]) -> tuple[dict[str, Any], SettleReason]:
    """The envelope a finished child's ROW stands for, and why it settled.

    Best effort by construction: the row keeps the summary, the error, the
    display cost and the tokens, but not the BYOK split (reported as 0 —
    ``by_child_byok`` has no consumer today, see ``folds/subagents.py``)."""
    row_status = str(run.get("status") or "")
    status = _RUN_STATUS_TO_ENVELOPE.get(row_status, "failed")
    if row_status == "heartbeat_lost":
        settle = settle_reason_for_lost_run(run)
    else:
        settle = settle_reason_for_envelope(status)
    envelope = {
        "status": status,
        "summary": run.get("output_summary") or "",
        "error": run.get("error_message") if status == "failed" else None,
        "sub_run_id": str(run["id"]),
        "cost_cents": float(run.get("cost_cents") or 0.0),
        "byok_cents": 0,
        "tokens_used": int(run.get("total_tokens") or 0),
    }
    return envelope, settle


__all__ = [
    "DeliveryOutcome",
    "deliver_subagent_result",
    "envelope_from_prior_run",
    "terminal_run_for_task",
]
