"""publish_issue_mirror — every publish batch is a managed work item.

User decision (2026-07-18): "发布就应该触发管理" — the todolist is the one
place work is controlled, so a distribution publish is mirrored into Issues
for its whole life, not just on failure:

  * batch dispatched            → issue created (todo/in_progress)
  * batch completed             → issue auto-transitions to done (zero noise),
                                  but a SCHEDULED batch stays open until its
                                  platform go-live time (see P1-2 below)
  * batch failed (retries done) → issue → blocked (the mockup's off-pipeline
                                  "incident" lane — a human decides what next)
  * pending_share               → the issue simply stays open as the reminder

路线 C compliance: the state machine stays in DBOS/task_tracking; this is a
ONE-WAY mirror (reads task_tracking, writes issues + the task row's
``issue_id`` business column). It never drives execution, so the two views
cannot drift into a second source of truth. Implemented as a scheduled
sweeper — not hooks inside the publish workflow — so the distribution
module's files stay untouched (another session's active territory) and
crash-lost batches still get mirrored. Eventual consistency (≤2 min) is fine
for a management view.

Idempotency: ``task_tracking.issue_id`` is both the back-link and the
create-once guard (stamped in the same sweep that creates the issue); the
issue's ``origin_id`` ('publish:{publish_task_id}') is the belt to that
suspender. Scheduled housekeeping runs do NOT create task_tracking rows
(路线 C rule 5 — mirrors stranded_issue_monitor).

Two holes closed together on 2026-08-10 (they MUST ship together — see below):

P1-1 — the 2-minute scan window swallowed every success. The sweep only
mirrored batches it caught mid-flight, because ``issue_status_for_phase``
returned ``None`` for ``completed`` ("nothing left to manage"). A real batch
finishes in ~60s, so the first sweep already sees ``completed`` and skips it
forever: production had ``task_tracking.issue_id = NULL`` on every successful
batch and only failures in the Issues table. A completed batch now DOES get
mirrored; it is created in a non-terminal status and closed by
``_sync_terminal_batches`` in the same sweep, so ``completed_at`` and the
sub-issue / pipeline hooks ride the one existing transition path.

P1-2 — for the session channel, "done" was a lie. The browser job only types
the time into the platform's UI and clicks confirm; the platform publishes
hours later. ``published_at`` records when *our* work finished, so mirroring
``completed`` straight to ``done`` would mark the item finished up to three
hours before anything is live. ``is_awaiting_platform_schedule`` therefore
holds the issue open until ``publish_tasks.scheduled_at`` passes. That
business column — not the DBOS state machine — is the source; the mirror
stays one-way (路线 C).

Shipping P1-1 without P1-2 would be worse than shipping neither: it trades
"silently invisible" for "silently claims done".

P1-3 — time-based release was still an assumption dressed as a conclusion. The
schedule passing says nothing about whether the platform reviewed it, refused
it, dropped the schedule, or the user deleted the post. The gate now needs a
READ-BACK: ``publish_readback`` opens the creator centre, finds the post, and
writes its verdict to ``publish_task_accounts.verify_state``. Only ``verified``
(or "nothing to verify") lets the issue close; ``not_live`` and ``abandoned``
send it to **blocked** with a typed reason, which is the state a scheduled
publish most needs to be able to reach.

The mirror stays a one-way reader throughout: it consults business columns that
another module owns and writes only ``issues`` + ``task_tracking.issue_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import BigInteger, Text, cast, func, select, text, update

from app.db.session import read_scope, write_scope
from app.models import Issues, PublishTaskAccounts, PublishTasks, TaskTracking

ORIGIN_KIND = "publish"

# Issue statuses that no longer need syncing.
_TERMINAL_ISSUE_STATUSES = frozenset({"done", "cancelled"})

# Only mirror recent batches — resurrecting months-old history as fresh
# todos would be noise, not management.
_LOOKBACK = "30 days"
_BATCH_LIMIT = 50


def build_publish_origin_id(publish_task_id: Any) -> str:
    """'publish:{publish_task_id}' — id stays a string (Snowflake bigint)."""
    return f"{ORIGIN_KIND}:{publish_task_id}"


def _as_aware(value: Any) -> Optional[datetime]:
    """Coerce a DB timestamp to an aware UTC datetime (None passes through).

    ``publish_tasks.scheduled_at`` is ``timestamptz`` so the driver hands back
    aware values, but a naive one must never silently compare as UTC-shifted
    local time — that would release the gate hours early or late.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_awaiting_platform_schedule(
    scheduled_at: Any, now: Optional[datetime] = None
) -> bool:
    """True while the platform has not reached the batch's go-live time.

    Our browser job finishing (``phase='completed'``) only means the schedule
    was accepted by the platform's compose page. Until ``scheduled_at``, the
    content is not live, so the mirrored issue must not read as done.
    """
    when = _as_aware(scheduled_at)
    if when is None:
        return False
    return when > (now or datetime.now(timezone.utc))


def build_scheduled_note(scheduled_at: Any) -> Optional[str]:
    """Issue description for a scheduled batch, or None when there is none.

    Written once at create time. The exact instant lives here (the issue row
    has nowhere else to put a business timestamp — ``issue_create_atomic``
    only accepts the columns it lists) so the to-do detail can answer "when
    does this go live?" without a second lookup.
    """
    when = _as_aware(scheduled_at)
    if when is None:
        return None
    return (
        "Scheduled publish — the platform puts this live at "
        f"{when.strftime('%Y-%m-%d %H:%M')} UTC ({when.isoformat()}). "
        "Our upload is already done; this item stays open until then."
    )


def issue_status_for_phase(
    phase: Optional[str],
    *,
    scheduled_at: Any = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Initial issue status when first mirroring a batch in ``phase``.

    ``completed`` is mirrored too (P1-1): a 61-second batch is already
    completed by the time the 2-minute sweeper first sees it, and returning
    None here meant successful publishes never entered the to-do at all. It
    lands in a NON-terminal status on purpose — ``_sync_terminal_batches``
    closes it through ``transition_status`` in the same sweep, which is what
    stamps ``completed_at`` and fires the sub-issue / pipeline hooks
    (``issue_create_atomic`` cannot write ``completed_at``).

    ⚠️ **两套词汇在这个函数里交汇，别把它们看成同一个词表**：入参 ``phase``
    是 ``task_tracking.phase``（batch 任务的执行阶段），返回值是 ``issues``
    的 status。两边**都**有一个叫 ``in_progress`` 的词，但含义无关 —— issue
    侧 ``in_progress`` 是合法状态；task 侧一个跑着的 workflow 实际停在
    ``processing``（见 ``unified_task_manager`` 的两写入方说明）。原先这里写
    ``if phase == "in_progress"``，比的是 task phase 却用了 issue 的词，那个
    分支从来没进去过。现在入参一侧一律走 ``ACTIVE_PHASES``。
    """
    from app.services.infra.unified_task_manager import ACTIVE_PHASES, TaskPhase

    if phase == TaskPhase.QUEUED.value:
        return "todo"
    if phase in ACTIVE_PHASES:  # dedup_check / processing / in_progress
        return "in_progress"
    if phase in ("failed", "lost"):
        return "blocked"
    if phase == "completed":
        # Non-terminal either way; the schedule gate below decides whether the
        # closing transition is allowed to run yet.
        return "in_progress"
    return None  # cancelled / unknown → no retroactive mirror


# Read-back verdicts, re-exported through the module that gates on them so the
# vocabulary has exactly one definition (``publish_readback`` owns it).
READBACK_NOT_REQUIRED = "not_required"


def readback_verdict(states: Any) -> str:
    """Collapse a batch's per-account ``verify_state`` values into one word.

    Pure. Returns ``not_required`` / ``pending`` / ``verified`` / ``not_live``
    / ``abandoned``.

    **Precedence is the design.** A batch is only as confirmed as its least
    confirmed account:

      1. ``not_live`` wins outright — one account whose post the platform
         refused is a thing a human must see, even if the other three are
         live. Averaging that away is how a partial failure becomes invisible.
      2. ``abandoned`` next — "we never managed to confirm this" is likewise
         something to surface, not to round up to done.
      3. ``pending`` next — still being worked on; hold the issue open.
      4. otherwise everything is settled and none of it is bad news.

    ``not_supported`` deliberately does NOT block: a platform without a
    read-back is OUR coverage gap, and holding the user's work item hostage to
    it would punish them for something they cannot act on. Such a batch falls
    back to P1-2's time-based release, which is what it had before.

    A row with no verify_state at all (NULL) counts as ``pending`` only when
    the batch has session rows awaiting one — the caller supplies exactly the
    rows that owe a verdict, so an empty input means "nothing to verify".
    """
    from app.workflows.publish_readback import (
        VERIFY_ABANDONED,
        VERIFY_NOT_LIVE,
        VERIFY_PENDING,
    )

    values = [s or VERIFY_PENDING for s in (states or [])]
    if not values:
        return READBACK_NOT_REQUIRED
    if VERIFY_NOT_LIVE in values:
        return VERIFY_NOT_LIVE
    if VERIFY_ABANDONED in values:
        return VERIFY_ABANDONED
    if VERIFY_PENDING in values:
        return VERIFY_PENDING
    return "verified"


def terminal_sync_action(
    phase: Optional[str],
    issue_status: Optional[str],
    *,
    scheduled_at: Any = None,
    now: Optional[datetime] = None,
    readback: str = READBACK_NOT_REQUIRED,
) -> Optional[str]:
    """Target issue status when a mirrored batch reaches a terminal phase.

    Two gates sit in front of ``done``, and they are different questions:

    * **P1-2, the clock.** A completed batch whose ``scheduled_at`` is still in
      the future is not done — the platform publishes it later. Return None and
      re-check next sweep.
    * **P1-3, the read-back.** Once the clock HAS passed, "done" still needs a
      real signal. ``verified`` closes it. ``not_live`` / ``abandoned`` send it
      to **blocked** — a post the platform refused, or one we could never
      confirm, is precisely the case a scheduled publish exists to manage, and
      it belongs in the incident lane where a human decides what happens next.
      ``pending`` holds.

    The ordering matters: the clock is checked first, so a batch that has not
    reached go-live is never blocked for "not being live yet".
    """
    from app.workflows.publish_readback import VERIFY_ABANDONED, VERIFY_NOT_LIVE

    if issue_status in _TERMINAL_ISSUE_STATUSES or issue_status == "blocked":
        return None
    if phase == "completed":
        if is_awaiting_platform_schedule(scheduled_at, now):
            return None
        if readback in (VERIFY_NOT_LIVE, VERIFY_ABANDONED):
            return "blocked"
        if readback == "pending":
            return None
        return "done"
    if phase in ("failed", "lost", "cancelled"):
        return "blocked" if phase != "cancelled" else "cancelled"
    return None


def _publish_task_join():
    """(metadata id expression, join condition) onto ``publish_tasks``.

    Shared by both sweep statements so the schedule gate reads exactly the
    same row the team attribution does — one idiom, no chance of the two
    queries resolving a batch to different publish_tasks rows.
    """
    publish_task_id_text = TaskTracking.metadata_.op("->>")("publish_task_id")
    join_cond = PublishTasks.id == cast(
        func.nullif(publish_task_id_text, ""), BigInteger
    )
    return publish_task_id_text, join_cond


def _unmirrored_stmt():
    """Batches not yet mirrored into an issue, joined to their publish_task
    row (if the metadata id resolves to a live one) for team_id attribution
    and the go-live schedule."""
    publish_task_id_text, join_cond = _publish_task_join()
    return (
        select(
            TaskTracking.dbos_workflow_id,
            TaskTracking.title,
            cast(TaskTracking.user_id, Text).label("user_id"),
            TaskTracking.phase,
            publish_task_id_text.label("publish_task_id"),
            PublishTasks.team_id,
            PublishTasks.scheduled_at,
        )
        .select_from(TaskTracking)
        .outerjoin(PublishTasks, join_cond)
        .where(
            TaskTracking.task_type == "publish",
            TaskTracking.issue_id.is_(None),
            TaskTracking.created_at > func.now() - text(f"interval '{_LOOKBACK}'"),
        )
        .order_by(TaskTracking.created_at.desc())
        .limit(_BATCH_LIMIT)
    )


def _readback_columns(publish_task_id_text):
    """(states, details) correlated aggregates over the batch's session rows.

    Scoped to ``status='success'`` AND ``channel='session'`` because those are
    exactly the rows a read-back is ever run for: an official/h5 row gets its
    ``published_url`` from the API that created it, and a failed row has
    nothing to confirm. Widening this would make every OAuth batch look like it
    were waiting on a verdict that will never arrive.

    ``array_agg`` keeps NULLs, which is what makes a never-attempted row read
    as ``pending`` in ``readback_verdict`` rather than vanishing.
    """
    task_id_bigint = cast(func.nullif(publish_task_id_text, ""), BigInteger)

    def agg(col):
        """Same subquery twice, differing only in the column aggregated —
        written once so the two can never drift onto different WHERE clauses
        and pair a verdict with some other row's reason."""
        return (
            select(func.array_agg(col))
            .where(
                PublishTaskAccounts.task_id == task_id_bigint,
                PublishTaskAccounts.status == "success",
                PublishTaskAccounts.channel == "session",
            )
            .correlate(TaskTracking)
            .scalar_subquery()
        )

    return (
        agg(PublishTaskAccounts.verify_state).label("readback_states"),
        agg(PublishTaskAccounts.verify_detail).label("readback_details"),
    )


def _mirrored_open_stmt():
    """Mirrored batches that reached a terminal phase but whose issue hasn't
    been synced to a terminal status yet.

    Carries ``scheduled_at`` so the closing transition can be held back until
    the platform's go-live time (P1-2), and the batch's read-back verdicts so
    that reaching go-live is not by itself enough to close it (P1-3). The join
    is an OUTER join: a batch whose publish_tasks row is gone must still close,
    not hang forever.
    """
    publish_task_id_text, join_cond = _publish_task_join()
    states, details = _readback_columns(publish_task_id_text)
    return (
        select(
            TaskTracking.dbos_workflow_id,
            TaskTracking.phase,
            TaskTracking.issue_id,
            Issues.status.label("issue_status"),
            PublishTasks.scheduled_at,
            states,
            details,
        )
        .select_from(TaskTracking)
        .join(Issues, Issues.id == TaskTracking.issue_id)
        .outerjoin(PublishTasks, join_cond)
        .where(
            TaskTracking.task_type == "publish",
            TaskTracking.issue_id.isnot(None),
            TaskTracking.phase.in_(["completed", "failed", "lost", "cancelled"]),
            Issues.status.notin_(["done", "cancelled", "blocked"]),
        )
        .limit(_BATCH_LIMIT)
    )


async def _mirror_new_batches() -> dict[str, int]:
    from app.repositories.issue_repository import get_issue_repository

    issues = get_issue_repository()
    counts = {"created": 0, "skipped": 0}
    async with read_scope() as session:
        rows = (await session.execute(_unmirrored_stmt())).mappings().all()
    for row in rows:
        try:
            scheduled_at = row.get("scheduled_at")
            status = issue_status_for_phase(row.get("phase"), scheduled_at=scheduled_at)
            publish_task_id = row.get("publish_task_id")
            if status is None or not publish_task_id:
                counts["skipped"] += 1
                continue
            origin_id = build_publish_origin_id(publish_task_id)
            existing = await issues.list_by_origin(ORIGIN_KIND, origin_id)
            if existing:
                issue_id = int(existing[0]["id"])
            else:
                payload: dict[str, Any] = {
                    "title": (row.get("title") or "Publish batch")[:500],
                    "status": status,
                    "origin_kind": ORIGIN_KIND,
                    "origin_id": origin_id,
                    # Mirror never assigns — 管控凭据, not a dispatch (立约).
                    "created_by_user_id": row["user_id"],
                }
                note = build_scheduled_note(scheduled_at)
                if note:
                    payload["description"] = note
                if row.get("team_id") is not None:
                    payload["team_id"] = int(row["team_id"])
                created = await issues.atomic_create(payload)
                issue_id = int(created["id"])
                counts["created"] += 1
            async with write_scope() as session:
                await session.execute(
                    update(TaskTracking)
                    .where(
                        TaskTracking.dbos_workflow_id == row["dbos_workflow_id"],
                        TaskTracking.issue_id.is_(None),
                    )
                    .values(issue_id=issue_id)
                )
        except Exception as exc:  # noqa: BLE001 — batch continues
            counts["skipped"] += 1
            logger.warning(
                f"[publish-mirror] mirror failed for {row.get('dbos_workflow_id')}: {exc!r}"
            )
    return counts


def build_readback_note(verdict: str, details: Any) -> Optional[str]:
    """The line appended to a work item blocked by the read-back. Pure.

    The typed reasons the browser produced (``[rejected] ...``,
    ``[under_review] ...``, ``[verification_abandoned] ...``) are carried
    through verbatim rather than summarised. They are the only thing that tells
    the user whether to appeal, wait, or re-publish — and "the publish did not
    go live" without which of those is a notification that cannot be acted on.
    """
    from app.workflows.publish_readback import VERIFY_NOT_LIVE

    reasons = [str(d).strip() for d in (details or []) if d and str(d).strip()]
    headline = (
        "Read-back says this publish is NOT live on the platform."
        if verdict == VERIFY_NOT_LIVE
        else "Go-live could not be confirmed after repeated read-back attempts."
    )
    if not reasons:
        return headline
    # De-duplicated but order-preserving: a broadcast batch hits N accounts
    # with the same outcome, and printing "[rejected] ..." four times adds
    # nothing.
    seen: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return headline + " " + " | ".join(seen)


async def _append_issue_note(issues, issue_id: int, note: str) -> None:
    """Append ``note`` to the issue description. Best-effort by contract.

    Appended, not replaced: ``build_scheduled_note`` already wrote when this
    was due to go live, and that is exactly the context someone reading a
    blocked item wants next to the reason it failed.

    Never allowed to abort the transition. The status change is the signal the
    user acts on; losing the explanatory line is bad, losing the block is
    worse — a batch that silently stayed 'in progress' is the failure mode this
    whole gate exists to remove.
    """
    try:
        current = await issues.get_by_id(issue_id)
        existing = (current or {}).get("description") or ""
        if note in existing:
            return
        merged = f"{existing}\n\n{note}".strip() if existing else note
        await issues.update(issue_id, {"description": merged[:4000]})
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(f"[publish-mirror] could not annotate issue {issue_id}: {exc!r}")


async def _sync_terminal_batches() -> dict[str, int]:
    from app.repositories.issue_repository import get_issue_repository
    from app.workflows.publish_readback import VERIFY_ABANDONED, VERIFY_NOT_LIVE

    issues = get_issue_repository()
    counts = {
        "synced": 0,
        "skipped": 0,
        "awaiting_schedule": 0,
        "awaiting_readback": 0,
        "blocked_by_readback": 0,
    }
    async with read_scope() as session:
        rows = (await session.execute(_mirrored_open_stmt())).mappings().all()
    for row in rows:
        try:
            scheduled_at = row.get("scheduled_at")
            verdict = readback_verdict(row.get("readback_states"))
            target = terminal_sync_action(
                row.get("phase"),
                row.get("issue_status"),
                scheduled_at=scheduled_at,
                readback=verdict,
            )
            if target is None:
                # Three separate counters so a held batch is legible in the
                # sweep log instead of hiding inside the generic skip bucket —
                # "waiting for the platform's clock" and "waiting for the
                # read-back" are different situations with different fixes.
                if row.get("phase") == "completed" and is_awaiting_platform_schedule(
                    scheduled_at
                ):
                    counts["awaiting_schedule"] += 1
                elif row.get("phase") == "completed" and verdict == "pending":
                    counts["awaiting_readback"] += 1
                else:
                    counts["skipped"] += 1
                continue
            if target == "blocked" and verdict in (VERIFY_NOT_LIVE, VERIFY_ABANDONED):
                # Annotate BEFORE the transition: the notification hooks that
                # ride on transition_status should be able to show the reason
                # rather than fire on a bare status change.
                note = build_readback_note(verdict, row.get("readback_details"))
                if note:
                    await _append_issue_note(issues, int(row["issue_id"]), note)
                counts["blocked_by_readback"] += 1
                logger.warning(
                    f"[publish-mirror] issue {row.get('issue_id')} blocked by "
                    f"read-back (verdict={verdict})"
                )
            await issues.transition_status(int(row["issue_id"]), target)
            counts["synced"] += 1
        except Exception as exc:  # noqa: BLE001 — batch continues
            counts["skipped"] += 1
            logger.warning(
                f"[publish-mirror] terminal sync failed for issue {row.get('issue_id')}: {exc!r}"
            )
    return counts


@DBOS.scheduled("*/2 * * * *")
@DBOS.workflow()
async def publish_issue_mirror_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """One sweep: mirror unmirrored batches, then sync terminal outcomes."""
    created = await _mirror_new_batches()
    synced = await _sync_terminal_batches()
    # Both halves report a "skipped" count; a plain {**a, **b} let the second
    # silently eat the first, so a run of mirror failures was invisible in the
    # log line meant to show them. Namespace them instead.
    result = {
        "created": created["created"],
        "mirror_skipped": created["skipped"],
        "synced": synced["synced"],
        "sync_skipped": synced["skipped"],
        "awaiting_schedule": synced["awaiting_schedule"],
        "awaiting_readback": synced["awaiting_readback"],
        "blocked_by_readback": synced["blocked_by_readback"],
    }
    # A scheduled batch sitting on the gate is NORMAL operation for hours —
    # logging it every 2 minutes would be pure noise, so the counter rides the
    # workflow's return value (durable in DBOS) and only real state changes
    # get a log line.
    if result["created"] or result["synced"] or result["mirror_skipped"]:
        logger.info(f"[publish-mirror] sweep: {result}")
    return result
