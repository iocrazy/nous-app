"""Give back inbox items a run claimed but never got an answer for (fh5 T5).

``InboxClaimHook`` stamps ``claimed_at`` / ``claimed_run_id`` BEFORE the LLM
call and injects the item into that call's ``messages``. When the call never
returns — ``AllModelsFailed`` escaped the step, the process died, the worker
was stopped — the injected copy is discarded with ``messages`` while the
stamp stays, and nothing rebuilds it: the item is not in the conversation
history and the ``inbox_claimed`` event only holds a clipped copy
(recon-d §1b/§1d). Before this module such a steer was simply lost.

Two ways to name the items to give back:

* **run-scoped** (``unclaim_for_run_stmt``): the recorder knows exactly which
  ids the step in flight claimed — ``RunRecorder._finish`` passes them when a
  run it closed ends ``failed``;
* **orphaned** (``unclaim_orphaned_by_run_stmt``): the writers that close a
  run without ``_finish`` (heartbeat_lost sweep, worker shutdown, liveness
  ``_mark_dead`` / startup reconcile) and a recovery run taking over its
  superseded predecessor derive it from the DB — the run's items whose
  ``claimed_step`` has no ``step_end`` event (``step_end`` is written only
  after the LLM answered, mig 453).

Both apply the same two guards:

* **re-delivery cap** — ``content.redelivered`` counts the give-backs; at
  ``REDELIVERY_CAP`` the item is expired with an ERROR instead. Without it a
  provider outage on a ``blocked`` issue (not terminal, so the idle-drain
  keeps dispatching it) would buy one billed failing turn per minute forever.
  A jsonb key rather than a column: no migration, and the renderers read
  named keys only, so the model never sees it (same as
  ``UNCLAIMED_ALERT_MARKER``);
* **terminal issue** — an item on a done / cancelled / hidden issue is left
  claimed: resurrecting it would show as live queue in ``pending_summary``
  and ``deliver_or_dispatch`` would refuse it anyway.

``cancelled`` and ``completed`` runs are never passed here: a cancel means
"stop", and re-delivering would restart what a person killed.

Plain async, not ``@DBOS.step``: every caller already runs inside a step or a
close function, and each statement is idempotent — it only matches rows still
claimed by the named run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple, Sequence

from loguru import logger
from sqlalchemy import (
    Integer,
    Text,
    and_,
    cast,
    exists,
    func,
    literal,
    not_,
    null,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import write_scope
from app.models import AgentRunInbox, AgentRunTranscriptEvents

#: ``content`` key counting how many times an item was given back.
REDELIVERED_KEY = "redelivered"
#: Give-backs allowed per item; the next one expires it instead.
REDELIVERY_CAP = 2
#: Same words as ``inbox_or_dispatch.TERMINAL_STATUSES`` (a unit test pins the
#: two together; importing it here would pull the service layer into the
#: repository).
TERMINAL_ISSUE_STATUSES = ("done", "cancelled")


class RedeliveryStmts(NamedTuple):
    """The two UPDATEs of one give-back, over disjoint rows (the counter
    splits them): ``expire`` for items at the cap, ``unclaim`` for the rest."""

    expire: Any
    unclaim: Any


@dataclass(frozen=True)
class RedeliveryOutcome:
    redelivered: tuple[int, ...] = ()
    expired: tuple[int, ...] = ()


def _redelivered():
    return func.coalesce(
        cast(AgentRunInbox.content[REDELIVERED_KEY].astext, Integer), 0
    )


def _not_on_a_finished_issue():
    from app.models import Issues

    finished = select(Issues.id).where(
        or_(
            Issues.status.in_(TERMINAL_ISSUE_STATUSES),
            Issues.hidden_at.isnot(None),
        )
    )
    return not_(
        and_(
            AgentRunInbox.target_kind == "issue",
            AgentRunInbox.target_id.in_(finished),
        )
    )


def _no_answer_at_claimed_step():
    """Matched on ``step`` only, never ``turn``: ``AgentRunner._step_ended``
    writes every ``step_end`` with a hard-coded ``turn=1`` while the hook
    stamps ``claimed_turn = ctx.turn``. A step number is unique within one run
    (one run = one turn), so ``(run_id, step)`` is the bracket. If runs ever
    span several turns, add the turn here AND fix ``_step_ended`` together."""
    ev = AgentRunTranscriptEvents
    return not_(
        exists(
            select(literal(1)).where(
                ev.run_id == AgentRunInbox.claimed_run_id,
                ev.event_type == "step_end",
                ev.step == AgentRunInbox.claimed_step,
            )
        )
    )


def _stmts(*where) -> RedeliveryStmts:
    returning = (
        AgentRunInbox.id,
        AgentRunInbox.target_kind,
        AgentRunInbox.target_id,
        _redelivered(),
    )
    base = (
        AgentRunInbox.expired_at.is_(None),
        AgentRunInbox.claimed_at.isnot(None),
        _not_on_a_finished_issue(),
        *where,
    )
    bumped = func.jsonb_build_object(cast(REDELIVERED_KEY, Text), _redelivered() + 1)
    expire = (
        update(AgentRunInbox)
        .where(*base, _redelivered() >= REDELIVERY_CAP)
        .values(expired_at=func.now())
        .returning(*returning)
    )
    unclaim = (
        update(AgentRunInbox)
        .where(*base, _redelivered() < REDELIVERY_CAP)
        .values(
            claimed_at=null(),
            claimed_run_id=null(),
            claimed_turn=null(),
            claimed_step=null(),
            content=AgentRunInbox.content.op("||", return_type=JSONB)(bumped),
        )
        .returning(*returning)
    )
    return RedeliveryStmts(expire=expire, unclaim=unclaim)


def unclaim_for_run_stmt(run_id: int, item_ids: Sequence[int]) -> RedeliveryStmts:
    """The given items, only while ``run_id`` still holds them."""
    return _stmts(
        AgentRunInbox.claimed_run_id == int(run_id),
        AgentRunInbox.id.in_([int(i) for i in item_ids]),
    )


def unclaim_orphaned_by_run_stmt(run_ids: Sequence[int]) -> RedeliveryStmts:
    """Every item these runs hold whose claiming step never answered."""
    return _stmts(
        AgentRunInbox.claimed_run_id.in_([int(r) for r in run_ids]),
        _no_answer_at_claimed_step(),
    )


async def _apply(stmts: RedeliveryStmts, *, reason: str) -> RedeliveryOutcome:
    async with write_scope() as session:
        expired = (await session.execute(stmts.expire)).all()
        redelivered = (await session.execute(stmts.unclaim)).all()
    for item_id, target_kind, target_id, count in expired:
        logger.error(
            f"[agent_run_inbox] item {item_id} on {target_kind} {target_id} was "
            f"given back {count} times and its run ended without an answer again "
            f"({reason}) — expired instead of re-delivered; the message it "
            "carried was never seen by the model"
        )
    for item_id, target_kind, target_id, _count in redelivered:
        logger.warning(
            f"[agent_run_inbox] item {item_id} on {target_kind} {target_id} "
            f"re-queued: its run ended without an answer ({reason})"
        )
    return RedeliveryOutcome(
        redelivered=tuple(int(r[0]) for r in redelivered),
        expired=tuple(int(r[0]) for r in expired),
    )


async def release_claims_for_run(
    run_id: int, item_ids: Sequence[int], *, reason: str
) -> RedeliveryOutcome:
    """Give back ``item_ids`` if ``run_id`` still holds them. Errors are logged,
    never raised: the caller is closing a run and must finish closing it."""
    if not item_ids:
        return RedeliveryOutcome()
    try:
        return await _apply(unclaim_for_run_stmt(run_id, item_ids), reason=reason)
    except Exception as err:  # noqa: BLE001 — a close path must not fail here
        logger.error(
            f"[agent_run_inbox] giving back items {list(item_ids)} of run "
            f"{run_id} ({reason}) failed: {err} — they stay claimed"
        )
        return RedeliveryOutcome()


async def release_orphaned_claims(
    run_ids: Sequence[int], *, reason: str
) -> RedeliveryOutcome:
    """Give back what these runs claimed at a step that never answered. Errors
    are logged, never raised (same reason as ``release_claims_for_run``)."""
    if not run_ids:
        return RedeliveryOutcome()
    try:
        return await _apply(unclaim_orphaned_by_run_stmt(run_ids), reason=reason)
    except Exception as err:  # noqa: BLE001 — a close path must not fail here
        logger.error(
            f"[agent_run_inbox] giving back the unanswered items of runs "
            f"{list(run_ids)} ({reason}) failed: {err} — they stay claimed"
        )
        return RedeliveryOutcome()


__all__ = [
    "REDELIVERED_KEY",
    "REDELIVERY_CAP",
    "RedeliveryOutcome",
    "RedeliveryStmts",
    "TERMINAL_ISSUE_STATUSES",
    "release_claims_for_run",
    "release_orphaned_claims",
    "unclaim_for_run_stmt",
    "unclaim_orphaned_by_run_stmt",
]
