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
from sqlalchemy import and_, func, insert, not_, select, tuple_, update

from app.db.session import read_scope, write_scope
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


def expire_stale_stmt(older_than: dt.datetime, *, skip_paused_issues: bool):
    """UPDATE … SET expired_at = now() for pending items older than the
    cutoff. With ``skip_paused_issues`` the issue targets whose issue has
    ``paused_at`` set are left alone (``NOT (kind='issue' AND id IN (paused))``
    — a non-issue target is never excluded). Pure builder: the shape is
    pinned by a compile test and executed by the schema-drift integration
    test."""
    from app.models import Issues

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
    if skip_paused_issues:
        paused_issue_ids = select(Issues.id).where(Issues.paused_at.isnot(None))
        stmt = stmt.where(
            not_(
                and_(
                    AgentRunInbox.target_kind == "issue",
                    AgentRunInbox.target_id.in_(paused_issue_ids),
                )
            )
        )
    return stmt


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
    """
    from app.models import Issues

    locked_issue_ids = select(Issues.id).where(Issues.execution_locked_at.isnot(None))
    return (
        select(
            AgentRunInbox.target_id,
            func.count().label("count"),
            func.min(AgentRunInbox.created_at).label("oldest_at"),
        )
        .where(AgentRunInbox.target_kind == "issue")
        .where(*_pending())
        .where(AgentRunInbox.target_id.notin_(locked_issue_ids))
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

    Best-effort by construction: without a unique index two SIMULTANEOUS
    enqueues can both miss. It is aimed at the sequential case it is needed
    for — a workflow body replayed after a crash — and says so rather than
    claiming an airtight guarantee it cannot make without a migration.
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
        ``content`` so no column and no migration are needed."""
        async with write_scope() as session:
            if dedupe_key:
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
        self, *, older_than: dt.datetime, skip_paused_issues: bool = True
    ) -> int:
        """Sweeper: a steer nobody claimed for a day is an orphan (its run
        ended before the next step boundary). Marked, never deleted.

        ``skip_paused_issues`` (phase 2a): an item queued on a PAUSED issue is
        waiting for resume, not orphaned — however old it gets.

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
                            older_than, skip_paused_issues=skip_paused_issues
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
    "get_agent_run_inbox_repository",
    "pending_issue_targets_stmt",
]
