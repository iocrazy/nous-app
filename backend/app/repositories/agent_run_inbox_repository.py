"""agent_run_inbox — the one queue into a running agent (mig 453, spec §1-③).

Not to be confused with ``inbox_repository`` (user notifications). Keyed by
the durable target (``issue`` / ``conversation``); ``claimed_at IS NULL AND
expired_at IS NULL`` is the queue. ``claim_stmt`` is a pure builder so its
shape (SKIP LOCKED, the two NULL guards, RETURNING) can be asserted by
compiling against the postgresql dialect — the mock boundary hid a wrong
bind type once already (jsonb_set, 2026-08-27).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional, Sequence

from loguru import logger
from sqlalchemy import (
    TIMESTAMP,
    Text,
    and_,
    case,
    cast,
    func,
    insert,
    not_,
    null,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from app.db.session import in_unit_of_work, read_scope, write_scope
from app.models import AgentRunInbox, ConversationAiMeta, Conversations

Target = tuple[str, int]


def _row(obj: Any) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


def _pending():
    return (AgentRunInbox.claimed_at.is_(None), AgentRunInbox.expired_at.is_(None))


def pending_summary_stmt(user_id: str):
    """SELECT target_id, count(*), min(created_at) FROM agent_run_inbox
    WHERE target_kind = 'issue' AND pending AND target_id IN (visible, unhidden
    issues) GROUP BY target_id. Visibility is the issue lists' own predicate —
    a queued count must never leak an issue the caller cannot open."""
    from app.models import Issues
    from app.repositories.issue_repository import visibility_predicate

    visible = (
        select(Issues.id)
        .where(visibility_predicate(user_id))
        .where(Issues.hidden_at.is_(None))
    )
    return (
        select(
            AgentRunInbox.target_id,
            func.count().label("count"),
            func.min(AgentRunInbox.created_at).label("oldest_at"),
        )
        .where(AgentRunInbox.target_kind == "issue")
        .where(*_pending())
        .where(AgentRunInbox.target_id.in_(visible))
        .group_by(AgentRunInbox.target_id)
    )


def expire_stale_stmt(
    older_than: dt.datetime,
    *,
    skip_paused_issues: bool,
    skip_parked_issues: bool = False,
    parked_floor: Optional[dt.datetime] = None,
):
    """UPDATE … SET expired_at = now() for pending items older than the
    cutoff. With ``skip_paused_issues`` the issue targets whose issue has
    ``paused_at`` set are left alone (``NOT (kind='issue' AND id IN (paused))``
    — a non-issue target is never excluded). Pure builder: the shape is
    pinned by a compile test and executed by the schema-drift integration
    test.

    ``skip_parked_issues`` (FH3 T1) also leaves alone the issues that are
    GENUINELY parked on the needs_input gate — see ``_parked_issue_ids``. The
    gate waits ``NEEDS_INPUT_RECV_TTL_HOURS`` (72 h) per round, so a wake-up
    queued while a person is being asked used to be thrown away at 24 h, long
    before the answer that would have claimed it. ``parked_floor`` is required
    with it: it is the upper bound that keeps a dead workflow's marker from
    pinning its items forever.
    """
    from app.models import Issues

    if skip_parked_issues and parked_floor is None:
        raise ValueError("skip_parked_issues needs a parked_floor (the TTL bound)")

    stmt = (
        update(AgentRunInbox)
        .where(*_pending())
        .where(AgentRunInbox.created_at < older_than)
        .values(expired_at=dt.datetime.now(dt.timezone.utc))
        # RETURNING, not rowcount: expiring an item DISCARDS a delivery nobody
        # ever consumed, and the sweep has to be able to name which targets
        # lost what (Task 7a defect 2 — the discard used to be silent).
        .returning(AgentRunInbox.target_kind, AgentRunInbox.target_id)
    )
    excluded = []
    if skip_paused_issues:
        excluded.append(select(Issues.id).where(Issues.paused_at.isnot(None)))
    if skip_parked_issues:
        excluded.append(_parked_issue_ids(parked_floor))
    for issue_ids in excluded:
        stmt = stmt.where(
            not_(
                and_(
                    AgentRunInbox.target_kind == "issue",
                    AgentRunInbox.target_id.in_(issue_ids),
                )
            )
        )
    return stmt


#: ``awaiting_input.since`` is written by ``input_gate.mark_awaiting_input`` as
#: ``datetime.isoformat()``. Only a value of that shape is cast: Postgres does
#: not promise to evaluate ``AND`` operands in order, so one malformed marker
#: would otherwise fail the whole UPDATE — every minute, for every issue.
_ISO_TIMESTAMP_PREFIX = r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"


def _parked_issue_ids(parked_floor: dt.datetime):
    """``SELECT id FROM issues`` that are parked on the needs_input gate and
    still inside its wait: the SQL form of ``is_parked_on_input`` plus a bound.

    * lock held — a workflow is (or was) running the issue;
    * ``execution_state.awaiting_input`` present with no ``answered_at`` —
      the question is still open;
    * ``awaiting_input.since`` newer than ``parked_floor`` (the sweeper passes
      now − (TTL + 1 h)) — the gate has not timed out yet.

    NOT the bare lock (recon §4): on 2026-09-25 production held five locks,
    four of them 16 days old, behind DBOS workflows that were all CANCELLED —
    no reaper releases those (both only look at PENDING/ENQUEUED). Skipping on
    the lock would leave their items unexpired AND undrained (the drain scan
    excludes locked issues too), silently, forever. The ``since`` bound ends
    that at the TTL; a marker without ``since`` is not skipped at all — fail
    toward expiry, which WARNs. No JOIN to ``dbos.*``: the bound is enough, and
    a new engine-schema access point is exactly what the raw-SQL rule forbids.
    """
    from app.models import Issues

    marker = Issues.execution_state["awaiting_input"]
    since = marker["since"].astext
    since_ts = case(
        (
            since.op("~")(_ISO_TIMESTAMP_PREFIX),
            cast(since, TIMESTAMP(timezone=True)),
        ),
        else_=null(),
    )
    return select(Issues.id).where(
        Issues.execution_locked_at.isnot(None),
        Issues.execution_state.has_key("awaiting_input"),
        marker["answered_at"].astext.is_(None),
        since_ts > parked_floor,
    )


def parked_workflow_ids_stmt(workflow_ids: Sequence[str], parked_floor: dt.datetime):
    """``SELECT dbos_workflow_id FROM issues`` for the given workflows whose
    issue is parked on the needs_input gate inside its wait — the same
    predicate as ``_parked_issue_ids``, keyed by the workflow instead.

    The health sweeper's 6h age backstop (FH3 T6) asks this before it cancels
    a same-version PENDING workflow: a parked ``execute_issue`` legitimately
    waits NEEDS_INPUT_RECV_TTL_HOURS, and killing it with a bare status UPDATE
    runs no ``finally`` — the lock and the marker stay forever."""
    from app.models import Issues

    return select(Issues.dbos_workflow_id).where(
        Issues.dbos_workflow_id.in_(list(workflow_ids)),
        Issues.id.in_(_parked_issue_ids(parked_floor)),
    )


def expire_agent_items_stmt(target_kind: str, target_id: int):
    """UPDATE … SET expired_at = now() for the PENDING items on one target that
    the AGENT scheduled for itself (``content.source.created_by = 'agent'``),
    RETURNING their ids.

    The idle-drain scan runs this on an issue waiting on a person (FH3 T1).
    Only the agent's own wake-ups match: a person's scheduled wake-up says
    ``created_by = 'user'``, and a plain steer or a ``subagent_result`` has no
    ``source`` at all (the historical shape — 21 of 27 items in production) —
    none of them is touched. Same cut as ``_fire_issue_wakeup``'s guard.
    """
    return (
        update(AgentRunInbox)
        .where(*_pending())
        .where(AgentRunInbox.target_kind == target_kind)
        .where(AgentRunInbox.target_id == int(target_id))
        .where(AgentRunInbox.content["source"]["created_by"].astext == "agent")
        .values(expired_at=dt.datetime.now(dt.timezone.utc))
        .returning(AgentRunInbox.id)
    )


def issue_activity_stmt(issue_id: int):
    """What the idle-drain scan needs to know about one issue: its status and
    the two columns ``is_parked_on_input`` reads."""
    from app.models import Issues

    return select(
        Issues.status, Issues.execution_state, Issues.execution_locked_at
    ).where(Issues.id == int(issue_id))


def pending_issue_targets_stmt(limit: int):
    """Every ISSUE target still holding an unclaimed, unexpired item, oldest
    first: ``(target_id, count, oldest_at)``.

    The sweeper's idle-drain reads this. Oldest first because a stranded item
    is somebody waiting — the one that has waited longest goes first — and the
    limit bounds one tick's work, not the backlog: what does not fit is picked
    up next minute.

    No aggregate over ``user_id``: Postgres has no ``min(uuid)``, and a stubbed
    session would happily compile one. The caller reads one pending row per
    target instead (bounded by the same limit).

    An issue holding ``execution_locked_at`` is EXCLUDED (Task 7b defect C).
    ``deliver_or_dispatch`` owns the three busy signals and this does not
    re-implement them — but there is a fourth state none of them covers:
    ``issue_lifecycle``'s in-turn drain has decided to run one more turn while
    the new ``agent_runs`` row does not exist yet, so no root run is running,
    nothing is paused, and no ``dispatching`` marker is up. The turn lock is
    what IS held across that gap (``execute_issue`` keeps it for the whole
    workflow), and on 2026-09-10 a sweeper tick landed in an 11 s one and
    bought a second billed turn on an item the drain had already taken.

    It belongs here rather than in ``_busy_reason``: this exclusion is the
    BACKSTOP declining to race the primary, not a general statement that a
    locked issue is busy — the reply path takes that same lock for itself.

    A PAUSED issue is excluded too (FH2 T1). ``deliver_or_dispatch`` answers
    ``inbox/already_enqueued`` for it (paused is one of its busy signals), and
    ``expire_stale_stmt(skip_paused_issues=True)`` deliberately never expires
    its items, so without this the scan re-read the same items every minute
    for as long as the pause lasted (prod issue 347469360799030, paused since
    2026-09-08). Nothing is lost by skipping them: resume either starts a
    fresh workflow whose in-turn drain and InboxClaimHook take the items, or
    clears the flag for the locked workflow that drains them itself — and a
    cleared ``paused_at`` puts the issue back in this scan on the next tick.
    ``_busy_reason`` keeps treating paused as busy: the comment and wake-up
    paths still need paused ⇒ inbox. Only the backstop's scan narrows.
    """
    from app.models import Issues

    locked_issue_ids = select(Issues.id).where(Issues.execution_locked_at.isnot(None))
    paused_issue_ids = select(Issues.id).where(Issues.paused_at.isnot(None))
    return (
        select(
            AgentRunInbox.target_id,
            func.count().label("count"),
            func.min(AgentRunInbox.created_at).label("oldest_at"),
        )
        .where(AgentRunInbox.target_kind == "issue")
        .where(*_pending())
        .where(AgentRunInbox.target_id.notin_(locked_issue_ids))
        .where(AgentRunInbox.target_id.notin_(paused_issue_ids))
        .group_by(AgentRunInbox.target_id)
        .order_by(func.min(AgentRunInbox.created_at))
        .limit(int(limit))
    )


def dedupe_lookup_stmt(target_kind: str, target_id: int, dedupe_key: str):
    """The live item on this target carrying ``content.dedupe_key``, if any.

    LIVE is pending OR CLAIMED — a claimed item was delivered, so re-queueing
    it would show the agent the same wake-up twice. An EXPIRED item is one
    nobody ever consumed (its run ended before the next step boundary), so it
    is deliberately NOT a match: the caller may queue again.

    Since mig 462 the guarantee is a CONSTRAINT, not this lookup:
    ``agent_run_inbox_dedupe_live_key`` is unique over
    ``(target_kind, target_id, content->>'dedupe_key')`` on live rows, with
    the same predicate this statement uses. The query stays because the
    sequential case it was written for — a workflow body replayed after a
    crash — is the common one, and answering it with a SELECT is cheaper than
    a failed INSERT plus a re-read. Two SIMULTANEOUS enqueues still both miss
    here; the index catches the loser and ``enqueue`` re-reads the winner.
    """
    return (
        select(AgentRunInbox)
        .where(AgentRunInbox.target_kind == target_kind)
        .where(AgentRunInbox.target_id == int(target_id))
        .where(AgentRunInbox.expired_at.is_(None))
        .where(AgentRunInbox.content["dedupe_key"].astext == dedupe_key)
        .order_by(AgentRunInbox.created_at, AgentRunInbox.id)
        .limit(1)
    )


#: ``content`` key the unclaimed-result alert stamps, so each item is alerted
#: once. A jsonb key rather than a column: no migration (fh4 ruling 4), and the
#: renderers read named keys only, so the model never sees it.
UNCLAIMED_ALERT_MARKER = "unclaimed_alerted_at"


def unclaimed_results_alert_stmt(*, older_than: dt.datetime, now: dt.datetime):
    """fh4 E2e: stamp every ``subagent_result`` that has sat unclaimed and
    unexpired since before ``older_than`` and was not stamped yet; RETURNING
    the stamped rows. One statement, so "select, then mark" cannot alert the
    same item twice across two overlapping ticks.

    A settled child whose result nobody consumed within the window is the
    signal the reaper's delivery and the drain backstop exist to prevent, so
    each one is worth an ERROR — once."""
    stamp = func.jsonb_build_object(
        cast(UNCLAIMED_ALERT_MARKER, Text), cast(now.isoformat(), Text)
    )
    return (
        update(AgentRunInbox)
        .where(AgentRunInbox.kind == "subagent_result")
        .where(*_pending())
        .where(AgentRunInbox.created_at < older_than)
        .where(not_(AgentRunInbox.content.has_key(UNCLAIMED_ALERT_MARKER)))
        .values(content=AgentRunInbox.content.op("||", return_type=JSONB)(stamp))
        .returning(
            AgentRunInbox.id,
            AgentRunInbox.target_kind,
            AgentRunInbox.target_id,
            AgentRunInbox.created_at,
        )
    )


def claim_stmt(
    targets: Sequence[Target], run_id: int, turn: int, step: int, now: dt.datetime
):
    """UPDATE … WHERE id IN (SELECT id … FOR UPDATE SKIP LOCKED) RETURNING *.

    Two claimers racing for the same target get disjoint rows: the inner
    SELECT locks what it returns and skips what a peer already holds.
    """
    locked = (
        select(AgentRunInbox.id)
        .where(
            tuple_(AgentRunInbox.target_kind, AgentRunInbox.target_id).in_(
                list(targets)
            )
        )
        .where(*_pending())
        .order_by(AgentRunInbox.created_at, AgentRunInbox.id)
        .with_for_update(skip_locked=True)
    )
    return (
        update(AgentRunInbox)
        .where(AgentRunInbox.id.in_(locked))
        .values(
            claimed_at=now,
            claimed_run_id=int(run_id),
            claimed_turn=turn,
            claimed_step=step,
        )
        .returning(AgentRunInbox)
    )


class AgentRunInboxRepository:
    async def enqueue(
        self,
        *,
        target_kind: str,
        target_id: int,
        user_id: str,
        kind: str,
        content: dict[str, Any],
        dedupe_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Queue one item. With ``dedupe_key`` the write is idempotent on that
        key: a caller whose delivery may be REPLAYED (a DBOS workflow body
        resumed after a crash — its writes are not step-recorded) gets the
        existing item back instead of a second copy. The key rides inside
        ``content``, so it still costs no column — mig 462 added the unique
        index over the jsonb expression, which is what makes two SIMULTANEOUS
        callers converge on one row instead of merely usually doing so."""
        if dedupe_key:
            async with write_scope() as session:
                existing = (
                    await session.execute(
                        dedupe_lookup_stmt(target_kind, int(target_id), dedupe_key)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    logger.info(
                        f"[agent_run_inbox] {target_kind} {target_id}: "
                        f"dedupe_key {dedupe_key} already queued — reusing item"
                    )
                    return _row(existing)
            content = {**content, "dedupe_key": dedupe_key}
        try:
            async with write_scope() as session:
                row = (
                    await session.execute(
                        insert(AgentRunInbox)
                        .values(
                            target_kind=target_kind,
                            target_id=int(target_id),
                            user_id=user_id,
                            kind=kind,
                            content=content,
                        )
                        .returning(AgentRunInbox)
                    )
                ).scalar_one()
                return _row(row)
        except IntegrityError:
            # The other writer of a simultaneous pair: mig 462's unique index
            # rejected this one. The INSERT ran in its own scope, so that
            # transaction is already unwound here — re-read the winner in a
            # fresh one.
            if not dedupe_key:
                # An insert carrying no dedupe_key cannot hit that index, so
                # this conflict is some OTHER constraint (the kind CHECK, a
                # dead claimed_run_id). Reinterpreting it as "somebody beat
                # me to it" would hand the caller an unrelated row.
                raise
            if in_unit_of_work():
                # No own transaction to discard: the conflict just aborted the
                # CALLER's, so the re-read would land on that same session and
                # raise PendingRollbackError. Doomed by construction, not a
                # second failure worth diagnosing — attempting it would only
                # print "the re-read failed too" and point the next reader at
                # the database. Same reading as AssetsRepository.create.
                raise
            winner = None
            try:
                async with read_scope() as retry:
                    winner = (
                        await retry.execute(
                            dedupe_lookup_stmt(target_kind, int(target_id), dedupe_key)
                        )
                    ).scalar_one_or_none()
            except Exception as err:  # noqa: BLE001
                logger.error(
                    f"[agent_run_inbox] {target_kind} {target_id}: dedupe_key "
                    f"{dedupe_key} conflicted and the re-read failed too: {err}"
                )
            if winner is None:
                # Nothing live carries this key, so the conflict was not the
                # race. Raise the original rather than invent a result.
                raise
            logger.info(
                f"[agent_run_inbox] {target_kind} {target_id}: dedupe_key "
                f"{dedupe_key} lost the race — reusing the winner's item"
            )
            return _row(winner)

    async def claim(
        self, *, targets: Sequence[Target], run_id: int, turn: int, step: int
    ) -> list[dict[str, Any]]:
        if not targets:
            return []
        now = dt.datetime.now(dt.timezone.utc)
        async with write_scope() as session:
            rows = (
                (await session.execute(claim_stmt(targets, run_id, turn, step, now)))
                .scalars()
                .all()
            )
        return sorted((_row(r) for r in rows), key=lambda r: (r["created_at"], r["id"]))

    async def list_for_target(
        self, *, target_kind: str, target_id: int, pending_only: bool, limit: int = 100
    ) -> list[dict[str, Any]]:
        stmt = (
            select(AgentRunInbox)
            .where(AgentRunInbox.target_kind == target_kind)
            .where(AgentRunInbox.target_id == int(target_id))
            .order_by(AgentRunInbox.created_at.desc(), AgentRunInbox.id.desc())
            .limit(limit)
        )
        if pending_only:
            stmt = stmt.where(*_pending())
        async with read_scope() as session:
            return [_row(r) for r in (await session.execute(stmt)).scalars().all()]

    async def oldest_pending_for_target(
        self, *, target_kind: str, target_id: int
    ) -> Optional[dict[str, Any]]:
        """The OLDEST unclaimed, unexpired item on this target, or None.

        Deliberately not ``list_for_target(limit=1)``: that one is a listing
        for the UI and orders ``created_at DESC``, so it hands back the NEWEST
        item — the opposite of what a drain wants, and a mismatch the caller
        could not see (Task 7a review M1). The order is in the name here.
        """
        stmt = (
            select(AgentRunInbox)
            .where(AgentRunInbox.target_kind == target_kind)
            .where(AgentRunInbox.target_id == int(target_id))
            .where(*_pending())
            .order_by(AgentRunInbox.created_at.asc(), AgentRunInbox.id.asc())
            .limit(1)
        )
        async with read_scope() as session:
            row = (await session.execute(stmt)).scalars().first()
        return _row(row) if row is not None else None

    async def pending_count(self, *, target_kind: str, target_id: int) -> int:
        async with read_scope() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(AgentRunInbox)
                        .where(AgentRunInbox.target_kind == target_kind)
                        .where(AgentRunInbox.target_id == int(target_id))
                        .where(*_pending())
                    )
                ).scalar_one()
            )

    async def pending_issue_targets(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Issues that still hold a pending item, oldest first (sweeper's
        idle-drain). System-wide: unlike ``pending_summary`` this is not a
        user's view, so no visibility predicate applies."""
        async with read_scope() as session:
            rows = (await session.execute(pending_issue_targets_stmt(limit))).all()
        return [
            {"target_id": int(tid), "count": int(n), "oldest_at": oldest}
            for tid, n, oldest in rows
        ]

    async def pending_summary(self, user_id: str) -> list[dict[str, Any]]:
        """Per-issue count of unclaimed items for the issues ``user_id`` can
        see (phase 2a §4: the "2 queued" chips). ``target_id`` stays int here;
        the router stringifies (Snowflake)."""
        async with read_scope() as session:
            rows = (await session.execute(pending_summary_stmt(user_id))).all()
        return [
            {"target_id": int(tid), "count": int(n), "oldest_at": oldest}
            for tid, n, oldest in rows
        ]

    async def expire_stale(
        self,
        *,
        older_than: dt.datetime,
        skip_paused_issues: bool = True,
        skip_parked_issues: bool = False,
        parked_floor: Optional[dt.datetime] = None,
    ) -> int:
        """Sweeper: a steer nobody claimed for a day is an orphan (its run
        ended before the next step boundary). Marked, never deleted.

        ``skip_paused_issues`` (phase 2a): an item queued on a PAUSED issue is
        waiting for resume, not orphaned — however old it gets.

        ``skip_parked_issues`` + ``parked_floor`` (FH3 T1): an item queued on an
        issue parked on the needs_input gate waits for the answer turn, until
        the gate's own TTL (``expire_stale_stmt`` / ``_parked_issue_ids``).

        Every expiry is WARNED with its target and count. A day-old unclaimed
        item is still an orphan and expiry is still the right end state, but
        the discard must be visible: during the 2026-09-10 acceptance a
        scheduled wake-up was stranded here while its schedule row reported
        ``fire_count=1``, and nothing anywhere would ever have said otherwise.
        """
        try:
            async with write_scope() as session:
                rows = (
                    await session.execute(
                        expire_stale_stmt(
                            older_than,
                            skip_paused_issues=skip_paused_issues,
                            skip_parked_issues=skip_parked_issues,
                            parked_floor=parked_floor,
                        )
                    )
                ).all()
        except Exception as err:  # noqa: BLE001
            logger.error(f"[agent_run_inbox] expire_stale failed: {err}")
            return 0

        by_target: dict[tuple[str, int], int] = {}
        for target_kind, target_id in rows:
            key = (str(target_kind), int(target_id))
            by_target[key] = by_target.get(key, 0) + 1
        for (target_kind, target_id), count in by_target.items():
            logger.warning(
                f"[agent_run_inbox] {target_kind} {target_id}: {count} pending "
                f"item(s) expired unclaimed (queued before {older_than.isoformat()}) "
                "— they were delivered to the inbox and nobody ever consumed them"
            )
        return len(rows)

    async def mark_unclaimed_results_alerted(
        self, *, older_than: dt.datetime
    ) -> list[dict[str, Any]]:
        """Stamp and return the ``subagent_result`` items that have waited
        unclaimed since before ``older_than`` (``unclaimed_results_alert_stmt``).
        Raises on a database error; the sweeper logs it and carries on."""
        async with write_scope() as session:
            rows = (
                await session.execute(
                    unclaimed_results_alert_stmt(
                        older_than=older_than, now=dt.datetime.now(dt.timezone.utc)
                    )
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    async def expire_agent_items_for_target(
        self, *, target_kind: str, target_id: int, reason: str
    ) -> int:
        """Expire the pending items the agent scheduled for itself on one
        target (``expire_agent_items_stmt``); return how many, WARN if any.

        Raises on a database error instead of answering 0: the caller is about
        to decide whether to buy a billed turn, and "nothing was expired"
        would send it straight to dispatching the very item this was meant to
        stop.
        """
        async with write_scope() as session:
            ids = (
                (
                    await session.execute(
                        expire_agent_items_stmt(target_kind, int(target_id))
                    )
                )
                .scalars()
                .all()
            )
        if ids:
            logger.warning(
                f"[agent_run_inbox] {target_kind} {target_id}: {len(ids)} pending "
                f"agent wake-up item(s) expired unclaimed ({reason}) — the issue "
                "is waiting on a person, so they will not start a turn"
            )
        return len(ids)

    async def issue_activity(self, issue_id: int) -> Optional[dict[str, Any]]:
        """``{status, execution_state, execution_locked_at}`` of one issue, or
        None when it does not exist (``issue_activity_stmt``)."""
        async with read_scope() as session:
            row = (
                (await session.execute(issue_activity_stmt(issue_id)))
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    # ── target lookups (the hook resolves its targets once per run) ──────

    async def issue_id_for_conversation(self, conversation_id: int) -> Optional[int]:
        """An issue's session is a conversation with ``context_type='issue'``;
        a run on that conversation also serves the issue's inbox."""
        async with read_scope() as session:
            cid = (
                await session.execute(
                    select(ConversationAiMeta.context_id)
                    .where(ConversationAiMeta.conversation_id == int(conversation_id))
                    .where(ConversationAiMeta.context_type == "issue")
                )
            ).scalar_one_or_none()
        try:
            return int(cid) if cid is not None else None
        except (TypeError, ValueError):
            return None

    async def conversation_target(
        self, conversation_id: int
    ) -> Optional[dict[str, Any]]:
        """``{id, created_by, archived_at}`` or None."""
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(
                        Conversations.id,
                        Conversations.created_by,
                        Conversations.archived_at,
                    ).where(Conversations.id == int(conversation_id))
                )
            ).first()
        if row is None:
            return None
        return {"id": int(row[0]), "created_by": str(row[1]), "archived_at": row[2]}


_repo: Optional[AgentRunInboxRepository] = None


def get_agent_run_inbox_repository() -> AgentRunInboxRepository:
    global _repo
    if _repo is None:
        _repo = AgentRunInboxRepository()
    return _repo


__all__ = [
    "AgentRunInboxRepository",
    "Target",
    "claim_stmt",
    "expire_agent_items_stmt",
    "expire_stale_stmt",
    "get_agent_run_inbox_repository",
    "issue_activity_stmt",
    "pending_issue_targets_stmt",
]
