"""G2 — DBOS-native approval gate for workflows.

Background
==========
G1 added persistence + UI surface for approval requests, but the
"resume" path was implicit: when the user approves, the agent's NEXT
chat turn re-reads the decision and continues. That works for chat
where the runner is invoked per-turn.

For long-running workflows (transcode chains, batch jobs requiring
human sign-off, scheduled tasks pending budget approval) we need a
real **pause-and-wait** primitive — the workflow yields, the
operator approves later (could be hours), the workflow resumes
exactly where it stopped.

DBOS provides this natively via three primitives:

  - ``DBOS.recv(topic, timeout)`` — block until a message lands on
    ``topic`` (or timeout). Survives worker restarts: if the worker
    crashes mid-recv, the workflow replays from the recv call on
    next pickup, without losing the message.
  - ``DBOS.send(workflow_id, payload, topic)`` — deliver a message
    to a paused workflow waiting on ``topic``.
  - ``DBOS.set_event`` / ``DBOS.get_event`` — alternative "named
    state" pattern (workflow exposes a named slot that callers
    can set from outside).

This module wraps the recv/send pair into a friendly approval-gate
API:

  await_approval_in_workflow(approval_id, ttl_seconds)
      → returns ApprovalDecision (approve / reject / expired)
      MUST be called from inside a @DBOS.workflow body.

  signal_approval_decision(workflow_id, approval_id, approved, note)
      → wakes a workflow blocked in await_approval_in_workflow.
      Called from the approve/reject HTTP endpoints.

The TOPIC string for each gate is the approval_request UUID so multiple
concurrent gates per workflow don't cross-deliver.

Integration today
=================
The chat path uses G1's surface-to-UI / next-turn-replay model. New
non-chat workflows (e.g. an `await_user_signoff_step`) should call
``await_approval_in_workflow`` directly. Approve/reject endpoints
already call ``signal_approval_decision`` — they're a no-op for the
chat path (no waiter exists) but light up automatically for any
workflow that opts in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Sentinel topic prefix — keeps approval messages from colliding with
# other DBOS.send usages in the future.
_TOPIC_PREFIX = "approval:"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    note: Optional[str]
    timed_out: bool = False  # True when await returned via timeout, not via send

    @classmethod
    def approve(cls, note: Optional[str] = None) -> "ApprovalDecision":
        return cls(approved=True, note=note)

    @classmethod
    def reject(cls, note: Optional[str] = None) -> "ApprovalDecision":
        return cls(approved=False, note=note)

    @classmethod
    def timeout(cls) -> "ApprovalDecision":
        return cls(approved=False, note=None, timed_out=True)


def _topic_for(approval_id: str) -> str:
    """Build a DBOS topic string from an approval-request UUID."""
    return f"{_TOPIC_PREFIX}{approval_id}"


def await_approval_in_workflow(
    approval_id: str,
    *,
    ttl_seconds: int = 24 * 3600,
) -> ApprovalDecision:
    """Pause the current DBOS workflow until ``approval_id`` is decided.

    MUST be called from inside a ``@DBOS.workflow`` body — DBOS.recv
    is recorded into the workflow checkpoint so a worker crash mid-wait
    can recover and continue waiting.

    Returns:
      - ApprovalDecision(approved=True/False, note=...) when the
        operator decided
      - ApprovalDecision.timeout() when ttl_seconds elapsed

    Note: this function uses ``DBOS.recv`` synchronously; DBOS handles
    the actual blocking + replay semantics. Caller body should look like:

        @DBOS.workflow()
        def my_workflow(payload):
            ...
            decision = await_approval_in_workflow(payload["approval_id"])
            if not decision.approved:
                return {"status": "rejected", "note": decision.note}
            ...continue work...
    """
    from dbos import DBOS

    topic = _topic_for(approval_id)
    # DBOS.recv blocks (or replays) until a matching DBOS.send lands.
    # When timeout fires, returns None.
    payload = DBOS.recv(topic, timeout_seconds=ttl_seconds)
    if payload is None:
        return ApprovalDecision.timeout()

    # Defensive parsing — payload comes from another process
    if isinstance(payload, dict):
        approved = bool(payload.get("approved"))
        note = payload.get("note")
        return ApprovalDecision(approved=approved, note=note)

    # Unknown shape → treat as reject so workflow doesn't proceed
    return ApprovalDecision(approved=False, note=f"malformed payload: {payload!r}")


def signal_approval_decision(
    *,
    workflow_id: str,
    approval_id: str,
    approved: bool,
    note: Optional[str] = None,
) -> bool:
    """Deliver an approval decision to a workflow paused in
    ``await_approval_in_workflow(approval_id)``.

    Returns True on apparent success (DBOS.send accepted), False on
    error. A False return is non-fatal for chat-style approvals where
    no workflow is actually waiting — it's only meaningful when a
    workflow opted into the pause-resume model.

    Idempotent: DBOS.send to a topic with no waiter just queues the
    message; if the workflow later calls recv, it picks up the queued
    message immediately. Safe to call even if the matching workflow
    already finished — DBOS drops messages destined for terminal
    workflows.
    """
    from loguru import logger
    from dbos import DBOS

    topic = _topic_for(approval_id)
    payload: dict[str, Any] = {"approved": approved, "note": note}
    try:
        DBOS.send(workflow_id, payload, topic=topic)
        return True
    except Exception as exc:
        # Most common: workflow_id doesn't exist (chat path — no
        # workflow registered an approval). That's fine; G1's
        # next-turn replay will resume.
        logger.debug(
            f"[approval_gate] send to workflow={workflow_id} "
            f"topic={topic} approved={approved} did not deliver: {exc}"
        )
        return False


__all__ = [
    "ApprovalDecision",
    "await_approval_in_workflow",
    "signal_approval_decision",
]
