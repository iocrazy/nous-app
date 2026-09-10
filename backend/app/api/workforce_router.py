"""Workforce dashboard endpoints.

Read paths:
  ``GET /api/v1/workforce/board`` — fat aggregate snapshot

Admin actions (I milestone):
  ``POST /api/v1/workforce/agents/{slug}/pause`` — set paused_reason
  ``POST /api/v1/workforce/agents/{slug}/resume`` — clear paused_reason
  ``POST /api/v1/workforce/agents/{slug}/clear-inbox`` — bulk-dismiss
  ``POST /api/v1/workforce/tasks/{task_id}/cancel`` — request task cancel

Auth: any logged-in user. The persistent agents are system presets
(no user_id) and the dashboard data is aggregate, not user-private.
This matches the Runs / Usage UIs today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import Text as SAText
from sqlalchemy import cast, column, func, literal, select
from sqlalchemy import update as sa_update

from app.core.deps import get_current_user
from app.db.session import read_scope, write_scope
from app.models import (
    AgentInbox,
    AgentOutbox,
    AgentRuns,
    AgentStateHistory,
    AgentWorkers,
    AiAgents,
    TaskTracking,
)
from app.repositories._orm_helpers import _plain
from app.repositories.agent_repository import get_agent_repository
from app.repositories.agent_workforce_repository import (
    TASK_KIND_AGENT,
    get_agent_workforce_repository,
    tt_row_to_task_shape,
)
from app.services.infra.dbos_orchestrator import is_launched as dbos_is_launched
from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

router = APIRouter(prefix="/workforce", tags=["workforce"])


def _serialize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce an ORM row mapping to the JSON-safe primitives the PostgREST
    path returned: enum → bare str, timestamptz → ISO-8601 str, uuid → str.
    BIGINT/int/Decimal pass through (FastAPI's encoder renders them as JSON
    numbers, exactly as PostgREST did)."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        value = _plain(value)
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


# Tunable for the healthz overall verdict. Inbox cadence is 10s + LLM-call
# latency, so 5 minutes of zero processed rows while the queue is non-empty is
# clearly broken. Only checked when persistent agents exist at all — an empty
# queue on a deployed-but-unused system is healthy, not stalled.
#
# Two tick-staleness thresholds used to sit here (_HEALTH_DEGRADED_AFTER_S /
# _HEALTH_DOWN_AFTER_S). They described the in-process WorkforceScheduler's
# last_tick timestamp; that scheduler is gone (2b-2 T3) and DBOS exposes no
# per-process equivalent, so they went with it rather than lingering as
# constants nothing reads.
_HEALTH_RECENT_WINDOW_S = 300

# The inbox dispatch tick fires every 10s (``@DBOS.scheduled("*/10 * * * * *")``
# in workflows/workforce_dispatch.py). A queued, never-dispatched agent_task
# older than three ticks has missed its turn repeatedly — that is a dispatch
# loop that is not dispatching, not a row that arrived a moment ago.
_DISPATCH_TICK_S = 10
_DISPATCH_STALE_AFTER_S = _DISPATCH_TICK_S * 3


async def _oldest_undispatched_age_s() -> Optional[float]:
    """Age in seconds of the oldest queued agent_task nobody has enqueued, or
    None when there is no such row.

    None means "nothing waiting", which is why it is None and not 0.0 — a
    caller must never confuse an empty result with a fresh one. Raises on a
    failed read so the probe can say it does not know instead of saying fine."""
    async with read_scope() as session:
        oldest = await session.scalar(
            select(func.min(TaskTracking.created_at))
            .where(TaskTracking.task_kind == TASK_KIND_AGENT)
            .where(TaskTracking.phase == "queued")
            .where(
                TaskTracking.metadata_.op("->>", return_type=SAText)(
                    cast(literal("dispatched_at"), SAText)
                ).is_(None)
            )
        )
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - oldest).total_seconds()


# ─── shared helpers ────────────────────────────────────────────────────


async def _resolve_persistent_agent(slug: str) -> dict[str, Any]:
    """Look up a persistent agent by slug. Raises 404 / 400 cleanly so
    the admin-action endpoints don't have to repeat the boilerplate."""
    repo = get_agent_repository()
    agent = await repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent '{slug}' not found")
    if not agent.get("persistent"):
        raise HTTPException(
            status_code=400,
            detail=f"agent '{slug}' is not a persistent worker",
        )
    return agent


@router.get("/board")
async def get_workforce_board(
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Aggregate snapshot for the Workforce dashboard.

    Returns:
        {
          "agents": [
            {
              "id": str, "slug": str, "name": str, "icon": str,
              "model": str, "persistent": bool,
              "worker": {state, current_task_id, state_changed_at, heartbeat_at}|None,
              "queue": {inbox_unread, inbox_reading, outbox_undelivered},
              "recent_runs": [{id, status, started_at, ended_at, cost_cents,
                               prompt_tokens, completion_tokens, trigger}, ...]
            }, ...
          ],
          "recent_state_history": [{agent_slug, from_state, to_state, trigger,
                                     changed_at, task_id}, ...]
        }
    """
    # 1. All persistent agents.
    async with read_scope() as session:
        agent_rows = (
            (
                await session.execute(
                    select(
                        AiAgents.id,
                        AiAgents.slug,
                        AiAgents.name,
                        AiAgents.icon,
                        AiAgents.model,
                        AiAgents.persistent,
                        AiAgents.paused_reason,
                    )
                    .where(AiAgents.persistent.is_(True))
                    .order_by(AiAgents.slug)
                )
            )
            .mappings()
            .all()
        )
    agents = [_serialize_row(a) for a in agent_rows]
    if not agents:
        return {"agents": [], "recent_state_history": []}

    agent_ids = [a["id"] for a in agents]
    agent_id_to_slug = {a["id"]: a["slug"] for a in agents}

    # 2. Worker rows for those agents (one shot via .in_).
    async with read_scope() as session:
        worker_rows = (
            (
                await session.execute(
                    select(
                        AgentWorkers.agent_id,
                        AgentWorkers.state,
                        AgentWorkers.current_task_id,
                        AgentWorkers.state_changed_at,
                        AgentWorkers.heartbeat_at,
                    ).where(AgentWorkers.agent_id.in_(agent_ids))
                )
            )
            .mappings()
            .all()
        )
    workers_by_agent: dict[str, dict[str, Any]] = {
        w["agent_id"]: w for w in (_serialize_row(r) for r in worker_rows)
    }

    # 3. Queue depths (one query per kind, range-filtered).
    inbox_unread_by_agent = await _count_inbox(agent_ids, "unread")
    inbox_reading_by_agent = await _count_inbox(agent_ids, "reading")
    outbox_undelivered_by_agent = await _count_outbox_undelivered(agent_ids)

    # 4. Recent runs per agent (top 5 each, one query — order + filter, then
    #    bucket client-side. Cap to ~50 total rows pulled.)
    async with read_scope() as session:
        run_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.agent_id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.started_at,
                        AgentRuns.ended_at,
                        AgentRuns.cost_cents,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.model,
                    )
                    .where(AgentRuns.agent_id.in_(agent_ids))
                    .order_by(AgentRuns.started_at.desc())
                    .limit(50)
                )
            )
            .mappings()
            .all()
        )
    runs_by_agent: dict[str, list[dict[str, Any]]] = {aid: [] for aid in agent_ids}
    for run in (_serialize_row(r) for r in run_rows):
        bucket = runs_by_agent.get(run["agent_id"])
        if bucket is not None and len(bucket) < 5:
            bucket.append(run)

    # 5. Assemble response per agent.
    response_agents: list[dict[str, Any]] = []
    for agent in agents:
        aid = agent["id"]
        response_agents.append(
            {
                "id": aid,
                "slug": agent["slug"],
                "name": agent.get("name") or agent["slug"],
                "icon": agent.get("icon"),
                "model": agent.get("model"),
                "persistent": bool(agent.get("persistent")),
                "paused_reason": agent.get("paused_reason"),
                "worker": workers_by_agent.get(aid),
                "queue": {
                    "inbox_unread": inbox_unread_by_agent.get(aid, 0),
                    "inbox_reading": inbox_reading_by_agent.get(aid, 0),
                    "outbox_undelivered": outbox_undelivered_by_agent.get(aid, 0),
                },
                "recent_runs": runs_by_agent.get(aid, []),
            }
        )

    # 6. Recent state transitions (across all persistent agents).
    async with read_scope() as session:
        history_rows = (
            (
                await session.execute(
                    select(
                        AgentStateHistory.agent_id,
                        AgentStateHistory.from_state,
                        AgentStateHistory.to_state,
                        AgentStateHistory.trigger,
                        AgentStateHistory.task_id,
                        AgentStateHistory.changed_at,
                    )
                    .where(AgentStateHistory.agent_id.in_(agent_ids))
                    .order_by(AgentStateHistory.changed_at.desc())
                    .limit(20)
                )
            )
            .mappings()
            .all()
        )
    recent_history = [
        {
            "agent_slug": agent_id_to_slug.get(row["agent_id"], "?"),
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "trigger": row["trigger"],
            "task_id": row.get("task_id"),
            "changed_at": row["changed_at"],
        }
        for row in (_serialize_row(r) for r in history_rows)
    ]

    return {
        "agents": response_agents,
        "recent_state_history": recent_history,
    }


async def _count_inbox(agent_ids: list[str], status: str) -> dict[str, int]:
    """Count inbox messages in ``status`` per recipient agent."""
    try:
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentInbox.recipient_agent_id)
                        .where(AgentInbox.recipient_agent_id.in_(agent_ids))
                        .where(AgentInbox.status == status)
                    )
                )
                .mappings()
                .all()
            )
        return _bucket_count([_serialize_row(r) for r in rows], "recipient_agent_id")
    except Exception as err:
        logger.warning(f"[workforce] inbox count failed (status={status}): {err}")
        return {}


async def _count_outbox_undelivered(agent_ids: list[str]) -> dict[str, int]:
    """Count undelivered outbox rows per sender agent."""
    try:
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentOutbox.sender_agent_id)
                        .where(AgentOutbox.sender_agent_id.in_(agent_ids))
                        .where(AgentOutbox.delivered.is_(False))
                    )
                )
                .mappings()
                .all()
            )
        return _bucket_count([_serialize_row(r) for r in rows], "sender_agent_id")
    except Exception as err:
        logger.warning(f"[workforce] outbox count failed: {err}")
        return {}


def _bucket_count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        out[v] = out.get(v, 0) + 1
    return out


# ─── admin actions (I milestone) ───────────────────────────────────────


class PauseAgentBody(BaseModel):
    reason: Optional[str] = None  # caller-supplied; default 'manual'


@router.post("/agents/{slug}/pause")
async def pause_agent(
    slug: str,
    body: PauseAgentBody = PauseAgentBody(),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Set ``paused_reason`` so RunRecorder.start refuses new runs.

    In-flight turns are NOT killed — they finish naturally. The pause
    only blocks NEW runs (chat or workforce) from starting.
    """
    agent = await _resolve_persistent_agent(slug)
    reason = (body.reason or "manual").strip()[:120]
    repo = get_agent_repository()
    await repo.update_fields(UUID(agent["id"]), {"paused_reason": reason})
    logger.info(f"[workforce] agent '{slug}' paused (reason={reason}) by user")
    return {"slug": slug, "paused_reason": reason, "status": "paused"}


@router.post("/agents/{slug}/resume")
async def resume_agent(
    slug: str,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Clear ``paused_reason`` so the agent accepts new runs again."""
    agent = await _resolve_persistent_agent(slug)
    repo = get_agent_repository()
    await repo.update_fields(UUID(agent["id"]), {"paused_reason": None})
    logger.info(f"[workforce] agent '{slug}' resumed by user")
    return {"slug": slug, "paused_reason": None, "status": "resumed"}


@router.post("/agents/{slug}/clear-inbox")
async def clear_inbox(
    slug: str,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Bulk-dismiss every unread/reading inbox row for this agent.

    Use when the queue gets stuck (mis-routed messages, runaway tests).
    Doesn't touch already-processed rows. Returns the count cleared.
    """
    agent = await _resolve_persistent_agent(slug)
    cleared = 0
    try:
        async with write_scope() as session:
            result = await session.execute(
                sa_update(AgentInbox)
                .where(AgentInbox.recipient_agent_id == agent["id"])
                .where(AgentInbox.status.in_(["unread", "reading"]))
                .values(status="dismissed", processed_at=datetime.now(timezone.utc))
                .returning(AgentInbox.id)
            )
            cleared = len(result.all())
    except Exception as err:
        logger.exception(f"[workforce] clear-inbox failed for {slug}: {err}")
        raise HTTPException(status_code=500, detail="clear-inbox failed")

    logger.info(f"[workforce] agent '{slug}' inbox cleared ({cleared} rows)")
    return {"slug": slug, "cleared": cleared}


@router.get("/healthz")
async def workforce_healthz() -> dict[str, Any]:
    """Operational health snapshot for the workforce runtime.

    Three signals:
      * dispatcher: is DBOS usable from this process, how many agent tasks are
        live, and how long the oldest never-enqueued queued task has waited.
        ``launched=False`` → ``down``; a backlog older than three dispatch
        ticks → ``degraded``.
      * recent_inbox_throughput: count of inbox rows processed in the
        last 5 min. Only flagged when there's at least one persistent
        agent — empty queues on a deployed-but-unused system are fine.
      * supabase: a light SELECT 1 on ai_agents to confirm the DB is
        reachable from the API container (workforce can't run without
        it, so this surfaces as ``down``).

    ⚠️ What ``launched`` does NOT say: that the scheduled dispatch tick is
    firing. The ticks run under the worker role; this endpoint answers from
    whichever process serves it. Two independent signals cover the stages it
    cannot see, and they watch DIFFERENT stages — do not treat either as a
    substitute for the other:
      * ``oldest_undispatched_age_s`` (task_tracking) — the enqueue loop.
      * the inbox throughput check (agent_inbox) — the stage before it,
        messages becoming task rows.
    Do not promote ``launched`` into a liveness claim it cannot make.

    Until phase 2b-2 T3 this section read ``app.state.workforce_scheduler``,
    which nothing had set since PR-D8 Phase 3 replaced the in-process
    ``WorkforceScheduler`` with DBOS-scheduled workflows. It never raised — it
    just answered ``down`` on every single call. An unconditional verdict is
    not a probe.

    Intentionally NOT auth-gated: monitors / NAS healthchecks need to
    poll without juggling tokens. The data exposed (counters + latency)
    is not user-private.
    """
    overall = "healthy"
    issues: list[str] = []
    response: dict[str, Any] = {"status": overall, "issues": issues}

    # 1. Dispatch runtime: DBOS reachability + the derived in-flight gauge.
    launched = dbos_is_launched()
    inflight: Optional[int] = None
    try:
        inflight = await DbosAgentWorkforcePool().inflight_count()
    except Exception as err:
        # A gauge we cannot read is a missing number, not a dead service — and
        # this endpoint must never 500 at a monitor.
        logger.warning(f"[workforce] inflight gauge unavailable: {err}")
        issues.append(f"inflight gauge unavailable: {type(err).__name__}")
        overall = "degraded"
    # The signal that actually watches the enqueue loop this endpoint's own
    # release wired up. The inbox throughput check further down reads
    # agent_inbox — the stage BEFORE this one — so it stays green while every
    # dispatch throws and gets swallowed per-order.
    oldest_age: Optional[float] = None
    try:
        oldest_age = await _oldest_undispatched_age_s()
    except Exception as err:
        logger.warning(f"[workforce] dispatch backlog probe failed: {err}")
        issues.append(f"dispatch backlog probe failed: {type(err).__name__}")
        overall = "degraded"
    response["dispatcher"] = {
        "engine": "dbos",
        "launched": launched,
        "inflight_agent_tasks": inflight,
        "oldest_undispatched_age_s": oldest_age,
    }
    if oldest_age is not None and oldest_age > _DISPATCH_STALE_AFTER_S:
        issues.append(
            f"dispatch stalled: a queued agent_task has waited "
            f"{oldest_age:.0f}s without being enqueued"
        )
        if overall == "healthy":
            overall = "degraded"
    if not launched:
        issues.append("dbos not launched — no workforce task can execute here")
        overall = "down"

    # 2. DB reachability + recent inbox throughput.
    try:
        # Are there any persistent agents? If not, "no recent processing" is
        # not a fault.
        since = datetime.now(timezone.utc) - timedelta(seconds=_HEALTH_RECENT_WINDOW_S)
        async with read_scope() as session:
            persistent_agents = (
                await session.execute(
                    select(func.count())
                    .select_from(AiAgents)
                    .where(AiAgents.persistent.is_(True))
                )
            ).scalar() or 0

            recent_processed = (
                await session.execute(
                    select(func.count())
                    .select_from(AgentInbox)
                    .where(AgentInbox.processed_at >= since)
                )
            ).scalar() or 0

            # Pending queue depth (unread + reading) gives us a "stuck queue"
            # signal: persistent agents + zero recent processing + non-empty
            # queue → scheduler is alive but not draining.
            pending_depth = (
                await session.execute(
                    select(func.count())
                    .select_from(AgentInbox)
                    .where(AgentInbox.status.in_(["unread", "reading"]))
                )
            ).scalar() or 0

        response["supabase"] = {
            "reachable": True,
            "persistent_agents": persistent_agents,
            "recent_processed_5m": recent_processed,
            "pending_depth": pending_depth,
        }

        if persistent_agents > 0 and pending_depth > 0 and recent_processed == 0:
            issues.append(
                f"queue stuck: {pending_depth} pending, no rows processed "
                f"in last {_HEALTH_RECENT_WINDOW_S}s"
            )
            if overall == "healthy":
                overall = "degraded"
    except Exception as err:
        response["supabase"] = {"reachable": False, "error": str(err)[:200]}
        issues.append(f"supabase unreachable: {type(err).__name__}")
        overall = "down"

    response["status"] = overall
    return response


@router.get("/agents/{slug}/detail")
async def get_agent_detail(
    slug: str,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Detail snapshot for one persistent agent — feeds the drawer view.

    Returns recent inbox messages (with sender + payload), recent outbox
    rows (with delivery status + payload), and recent runs (with
    input/output summaries + cost). Bigger payloads than /board so it's
    paged separately and only loaded when the user opens the drawer.
    """
    agent = await _resolve_persistent_agent(slug)
    aid = agent["id"]

    async with read_scope() as session:
        # Inbox: most recent 20, all statuses, with payload + sender.
        inbox_rows = (
            (
                await session.execute(
                    select(
                        AgentInbox.id,
                        AgentInbox.sender_kind,
                        AgentInbox.sender_user_id,
                        AgentInbox.sender_agent_id,
                        AgentInbox.message_type,
                        AgentInbox.payload,
                        AgentInbox.status,
                        AgentInbox.priority,
                        AgentInbox.created_at,
                        AgentInbox.processed_at,
                        AgentInbox.reply_to_message_id,
                        AgentInbox.dedup_key,
                    )
                    .where(AgentInbox.recipient_agent_id == aid)
                    .order_by(AgentInbox.created_at.desc())
                    .limit(20)
                )
            )
            .mappings()
            .all()
        )

        # Outbox: most recent 20 SENT by this agent.
        outbox_rows = (
            (
                await session.execute(
                    select(
                        AgentOutbox.id,
                        AgentOutbox.recipient_kind,
                        AgentOutbox.recipient_user_id,
                        AgentOutbox.recipient_agent_id,
                        AgentOutbox.message_type,
                        AgentOutbox.payload,
                        AgentOutbox.task_id,
                        AgentOutbox.delivered,
                        AgentOutbox.delivered_at,
                        AgentOutbox.created_at,
                    )
                    .where(AgentOutbox.sender_agent_id == aid)
                    .order_by(AgentOutbox.created_at.desc())
                    .limit(20)
                )
            )
            .mappings()
            .all()
        )

        # Runs: most recent 20 with full summaries (capped server-side at 500
        # chars by RunRecorder, so the response stays bounded).
        run_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.model,
                        AgentRuns.provider,
                        AgentRuns.started_at,
                        AgentRuns.ended_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.cost_cents,
                        AgentRuns.input_summary,
                        AgentRuns.output_summary,
                        AgentRuns.error_code,
                        AgentRuns.error_message,
                    )
                    .where(AgentRuns.agent_id == aid)
                    .order_by(AgentRuns.started_at.desc())
                    .limit(20)
                )
            )
            .mappings()
            .all()
        )

    return {
        "agent": {
            "id": aid,
            "slug": agent["slug"],
            "name": agent.get("name") or agent["slug"],
            "icon": agent.get("icon"),
            "model": agent.get("model"),
            "persistent": bool(agent.get("persistent")),
            "paused_reason": agent.get("paused_reason"),
        },
        "inbox": [_serialize_row(r) for r in inbox_rows],
        "outbox": [_serialize_row(r) for r in outbox_rows],
        "runs": [_serialize_row(r) for r in run_rows],
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark an in-flight or queued task as cancelled.

    The DB transition is the source of truth — once
    ``lifecycle_status='cancelled'``, the worker checks (and the
    RunRecorder cancel poll) will refuse to keep going. Already-done
    tasks are left alone.
    """
    workforce = get_agent_workforce_repository()
    task = await workforce.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.get("lifecycle_status") in ("done", "failed", "cancelled"):
        return {
            "task_id": str(task_id),
            "lifecycle_status": task["lifecycle_status"],
            "note": "task already in terminal state — no-op",
        }
    try:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="cancelled",
            error_code="user_cancel",
            error_message="Cancelled via workforce admin endpoint",
        )
    except Exception as err:
        logger.exception(f"[workforce] cancel-task failed for {task_id}: {err}")
        raise HTTPException(status_code=500, detail="cancel failed")

    logger.info(f"[workforce] task {task_id} cancelled by user")
    return {"task_id": str(task_id), "lifecycle_status": "cancelled"}


# ─── Delegate sub-task lookup ───────────────────────────────────────────
#
# The chat UI's sub-task cards (TapNow Step B) need to follow the
# lifecycle of a Delegate dispatch from queued → in_progress → done. The
# Delegate tool's response carries ``inbox_message_id``; this endpoint
# resolves that to the matching agent_tasks row + the sub-agent's outbox
# response when terminal. Frontend pairs this with a Realtime subscription
# on agent_tasks (filter by inbox_message_id) for live updates.


@router.get("/tasks/by-inbox/{inbox_message_id}")
async def get_task_by_inbox(
    inbox_message_id: UUID,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Resolve an ``inbox_message_id`` (returned by the Delegate tool)
    to the sub-agent's task lifecycle + final outbox response.

    Returns:
        ``task``: the agent_tasks row owning this inbox message
            (lifecycle_status / started_at / ended_at / error_*).
            ``None`` when the recipient agent hasn't picked it up yet.
        ``outbox_response``: the sub-agent's reply outbox row when the
            task is done. ``None`` for queued / in_progress.

    Auth: only callers who can see the inbox message itself — i.e. the
    sender_user_id of the inbox row (the user who triggered the chat
    turn that fired Delegate). System-preset agent presets have no
    user_id so chat-triggered Delegates have a sender_user_id we can
    check against.
    """
    async with read_scope() as session:
        inbox = (
            (
                await session.execute(
                    select(
                        AgentInbox.id,
                        AgentInbox.recipient_agent_id,
                        AgentInbox.sender_kind,
                        AgentInbox.sender_user_id,
                        AgentInbox.sender_agent_id,
                        AgentInbox.reply_to_message_id,
                    )
                    .where(AgentInbox.id == str(inbox_message_id))
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not inbox:
        raise HTTPException(
            status_code=404,
            detail=f"inbox message {inbox_message_id} not found",
        )

    # Auth: the user must be the sender of the inbox message. Chat
    # turns set sender_user_id; agent-to-agent delegates set
    # sender_agent_id and we don't expose those here (admin-only via
    # the workforce drawer).
    sender_user_id = inbox.get("sender_user_id")
    user_id_str = str(getattr(user, "id", user))
    if not sender_user_id or str(sender_user_id) != user_id_str:
        raise HTTPException(
            status_code=403,
            detail="not authorized to view this delegate task",
        )

    # Look up the task — may not exist yet if the recipient hasn't
    # ticked. Return None rather than 404 so the frontend can show
    # "queued" until the worker picks it up.
    # A4: agent_tasks → task_tracking WHERE task_kind='agent_task'.
    async with read_scope() as session:
        task_row = (
            (
                await session.execute(
                    select(
                        TaskTracking.dbos_workflow_id,
                        TaskTracking.agent_id,
                        TaskTracking.phase,
                        TaskTracking.started_at,
                        TaskTracking.completed_at,
                        TaskTracking.error_code,
                        TaskTracking.error_msg,
                        TaskTracking.created_at,
                        TaskTracking.inbox_message_id,
                        TaskTracking.metadata_.label("metadata"),
                    )
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.inbox_message_id == str(inbox_message_id))
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    task = tt_row_to_task_shape(_serialize_row(task_row) if task_row else None)

    # Sub-agent's reply (if any). The sub-agent writes to outbox with
    # ``reply_to_message_id`` pointing back at our inbox row, so we can
    # find the response without a task→outbox join.
    outbox_response: Optional[dict[str, Any]] = None
    if task and task.get("lifecycle_status") in ("done", "failed"):
        async with read_scope() as session:
            outbox_rows = (
                (
                    await session.execute(
                        select(
                            AgentOutbox.id,
                            AgentOutbox.sender_agent_id,
                            AgentOutbox.message_type,
                            AgentOutbox.payload,
                            AgentOutbox.created_at,
                            AgentOutbox.delivered,
                            AgentOutbox.delivered_at,
                        )
                        # NOTE: agent_outbox has no reply_to_message_id column
                        # (it lives on agent_inbox — mig 159). This filter is a
                        # pre-existing bug preserved byte-for-byte from the
                        # supabase-py path: ``column(...)`` renders the same
                        # unqualified predicate the old ``.eq(...)`` did, so the
                        # runtime outcome is identical (works only if prod has
                        # the column as drift; errors the same way otherwise).
                        .where(column("reply_to_message_id") == str(inbox_message_id))
                        .order_by(AgentOutbox.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .all()
            )
        if outbox_rows:
            outbox_response = _serialize_row(outbox_rows[0])

    return {
        "inbox_message_id": str(inbox_message_id),
        "task": task,
        "outbox_response": outbox_response,
    }
