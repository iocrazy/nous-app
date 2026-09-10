"""Delegate tool — cross-agent dispatch primitive.

The agent calls ``Delegate(agent_slug=..., prompt=...)`` to hand a
sub-task to another persistent agent. The tool:

    1. Resolves the target agent by slug (must exist, must be persistent).
    2. Rejects self-dispatch and cycles (parent chain check).
    3. Enforces a max delegation depth (cuts pathological recursion).
    4. Writes one inbox row for the target + one outbox row for the
       caller (audit + Realtime). Both link to the caller's run via
       parent_run_id so cost rollups stay tree-aware.
    5. Returns a status payload to the LLM. With ``await=false``
       (default) it's fire-and-forget. With ``await=true`` (F milestone)
       it polls the target's task lifecycle until terminal and embeds
       the result content in the response so the caller's LLM can
       reason on it in the same turn.

Notes on design choices:
- We reject self-delegate hard (same caller_agent_id == target_agent_id).
  Same-agent recursion needs explicit task spawning, not Delegate.
- Cycle protection (J): walks ``agent_runs.parent_run_id`` to root and
  rejects when the target agent already appears upstream. Catches
  ping-pong loops that depth-only protection misses.
- Awaited path is implemented via polling, not LISTEN/NOTIFY: Supabase
  pgbouncer transaction pooling drops LISTEN, and polling at 1-5s
  intervals is fine for the M3 cadence.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from time import monotonic
from typing import Any, Deque, Dict, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import AgentRepository, get_agent_repository
from app.repositories.agent_workforce_repository import (
    TASK_KIND_AGENT,
    AgentWorkforceRepository,
    get_agent_workforce_repository,
    tt_row_to_task_shape,
)

# Maximum delegation depth from a single root run. 3 levels is enough for
# meaningful workforce composition (planner → executor → critic) without
# letting a buggy agent runaway-recurse into the inbox.
MAX_DELEGATION_DEPTH = 3

# Default and ceiling for ``await=true`` polling. The caller's LLM-call
# wallclock is already heavy; we don't want a stuck delegation to hold
# its asyncio.Lock forever. Caller can request a custom value via the
# ``await_timeout_seconds`` arg, capped at MAX.
DEFAULT_AWAIT_TIMEOUT_S = 60.0
MAX_AWAIT_TIMEOUT_S = 180.0
TERMINAL_LIFECYCLE_STATES = {"done", "failed", "cancelled"}

# Q milestone: per-caller-agent rate limit on Delegate dispatch.
#
# An LLM in a tool-use loop can otherwise emit dozens of Delegate calls
# in seconds and flood the workforce inbox. Sliding window: keep
# timestamps for each caller_agent_id, reject when the count in the
# trailing window exceeds the cap. Process-local; we run a single
# uvicorn worker so this matches deployment shape.
#
# 30 calls / 60s is well above normal workflows (coordinator typically
# does 1-2 per turn every ~30s) but catches runaway loops fast.
RATE_LIMIT_WINDOW_S = 60.0
RATE_LIMIT_MAX_CALLS = 30
_dispatch_history: dict[UUID, Deque[float]] = defaultdict(deque)


def _check_rate_limit(caller_agent_id: UUID) -> Optional[Dict[str, Any]]:
    """Sliding-window rate check. Returns an error payload when the
    caller is over the limit, or ``None`` when they're under it.

    Side effect: appends ``now`` to the caller's history when the
    request is allowed; prunes expired entries on every call.
    """
    now = monotonic()
    history = _dispatch_history[caller_agent_id]
    cutoff = now - RATE_LIMIT_WINDOW_S
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= RATE_LIMIT_MAX_CALLS:
        retry_in = max(0.0, history[0] + RATE_LIMIT_WINDOW_S - now)
        return {
            "error": (
                f"rate limit: {RATE_LIMIT_MAX_CALLS} Delegate calls per "
                f"{RATE_LIMIT_WINDOW_S:.0f}s window per caller agent — "
                "back off before dispatching more"
            ),
            "retry_after_seconds": round(retry_in, 1),
            "window_seconds": int(RATE_LIMIT_WINDOW_S),
            "limit": RATE_LIMIT_MAX_CALLS,
        }
    history.append(now)
    return None


class DelegateToolService:
    """Per-turn service: caller context is baked in at construction.

    The chat-service wiring layer constructs one of these per agent turn,
    populating the caller's identity from the active run, then injects
    it into the AgentRunner. The runner dispatches ``Delegate`` calls to
    ``execute()``.
    """

    def __init__(
        self,
        *,
        caller_agent_id: UUID,
        caller_user_id: UUID,
        # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string.
        parent_run_id: Optional[str],
        agent_depth: int = 0,
        agent_repo: Optional[AgentRepository] = None,
        workforce_repo: Optional[AgentWorkforceRepository] = None,
        parent_recorder: Optional[Any] = None,
    ) -> None:
        self.caller_agent_id = caller_agent_id
        self.caller_user_id = caller_user_id
        self.parent_run_id = parent_run_id
        self.agent_depth = agent_depth
        self.agent_repo = agent_repo or get_agent_repository()
        self.workforce_repo = workforce_repo or get_agent_workforce_repository()
        # The turn's RunRecorder, bound by AgentRunner._bind_turn_recorder once
        # the row exists. See active_parent_run_id for why it, not the
        # constructor value, is what a delegated child hangs off.
        self.parent_recorder = parent_recorder

    @property
    def active_parent_run_id(self) -> Optional[str]:
        """The run a delegation issued NOW hangs off — resolved at call time.

        The constructor value answers a DIFFERENT question: which run this
        turn is itself a child of. ``build_agent_runner_stack`` passes ``None``
        there for a top-of-tree turn, so on every root issue / chat run the
        constructor value is empty — and three things went out wrong because
        of it (Task 7a defect 5, same family as defect 1 in
        ``SubAgentTaskService``): the inbox payload's ``parent_run_id`` was
        null so the child's cost never rolled up; the root abort registry
        never registered the child so a cancel did not fan out; and
        ``_detect_cycle`` short-circuits on a falsy value, which turned cycle
        protection OFF for exactly the runs users start.

        The recorder's row IS the run currently executing, so that is the
        parent. The constructor value stays the fallback for callers with no
        recorder — the workforce worker rebuilds this service from an inbox
        payload whose ``parent_run_id`` is already the right one.
        """
        rid = getattr(self.parent_recorder, "run_id", None)
        return (
            str(rid)
            if rid
            else (str(self.parent_run_id) if self.parent_run_id else None)
        )

    async def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Audit #4 fail-closed gate: the inbox→worker execution chain is not
        # proven end to end on the deployed worker, so a queued delegation may
        # never run. Refuse rather than silently accept — even if the tool
        # somehow reaches here while ungated (model replaying an old tool_call,
        # worker sub-delegation). See delegate_feature.py.
        from app.services.workforce.delegate_feature import (
            delegate_feature_enabled,
        )

        if not delegate_feature_enabled():
            return {
                "error": (
                    "delegation is not enabled (FEATURE_WORKFORCE_DELEGATE off); "
                    "the inbox→worker execution chain has not been verified on "
                    "the deployed worker, so a delegated task may never run"
                ),
            }

        slug = (args.get("agent_slug") or args.get("agent") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        if not slug:
            return {"error": "agent_slug required"}
        if not prompt:
            return {"error": "prompt required"}

        # Rate limit before depth — both are cheap, but rate limit
        # lets us fast-fail a runaway loop without spending DB roundtrips.
        rl_error = _check_rate_limit(self.caller_agent_id)
        if rl_error is not None:
            logger.warning(
                f"[delegate] rate-limit hit for caller={self.caller_agent_id} "
                f"(slug={slug})"
            )
            return rl_error

        # Depth check next — cheap, no DB roundtrip needed.
        if self.agent_depth >= MAX_DELEGATION_DEPTH:
            return {
                "error": (
                    f"max delegation depth ({MAX_DELEGATION_DEPTH}) reached; "
                    "this would create a >3-level dispatch chain"
                ),
                "depth": self.agent_depth,
            }

        target = await self.agent_repo.get_by_slug(slug)
        if not target:
            return {"error": f"unknown agent slug: {slug}"}

        target_agent_id = UUID(target["id"])

        if target_agent_id == self.caller_agent_id:
            return {
                "error": "cannot delegate to self; spawn a sub-task instead",
            }

        if not target.get("persistent"):
            return {
                "error": (
                    f"agent '{slug}' is not configured as a persistent worker "
                    "(ai_agents.persistent=false). Persist it first or pick "
                    "a different agent."
                )
            }

        # Cycle detection: walk parent_run_id chain to root and reject
        # if any ancestor run's agent already appears in the chain.
        # Without this, A→B→A→B can stay under MAX_DELEGATION_DEPTH and
        # ping-pong indefinitely. We also catch broken/loopy data via a
        # hard cap on walk depth.
        cycle = await self._detect_cycle(target_agent_id=target_agent_id)
        if cycle is not None:
            return {
                "error": (
                    f"cycle detected: target '{slug}' already appears in the "
                    f"delegation chain (ancestor run {cycle})"
                ),
                "agent_slug": slug,
                "cycle_run_id": str(cycle),
            }

        # Forwarded options
        title = args.get("title")
        priority = int(args.get("priority") or 5)
        dedup_key = args.get("dedup_key")
        await_result = bool(args.get("await") or False)
        await_timeout = float(
            args.get("await_timeout_seconds") or DEFAULT_AWAIT_TIMEOUT_S
        )
        if await_timeout <= 0:
            await_timeout = DEFAULT_AWAIT_TIMEOUT_S
        await_timeout = min(await_timeout, MAX_AWAIT_TIMEOUT_S)

        payload = {
            "title": title,
            "prompt": prompt,
            "delegated_by": str(self.caller_agent_id),
            "delegated_at_depth": self.agent_depth,
            "await": await_result,
            "parent_run_id": self.active_parent_run_id,
        }

        inbox_row = await self.workforce_repo.enqueue_inbox(
            recipient_agent_id=target_agent_id,
            sender_kind="agent",
            sender_user_id=self.caller_user_id,
            sender_agent_id=self.caller_agent_id,
            message_type="task",
            payload=payload,
            priority=priority,
            dedup_key=dedup_key,
        )

        outbox_row = await self.workforce_repo.enqueue_outbox(
            sender_agent_id=self.caller_agent_id,
            recipient_kind="agent",
            recipient_agent_id=target_agent_id,
            recipient_user_id=self.caller_user_id,
            message_type="task",
            payload=payload,
        )

        if not inbox_row:
            return {
                "error": "failed to enqueue inbox message",
                "agent_slug": slug,
            }

        # Wave J (J3): register the child run in the root abort registry
        # so cancelling the root run fans out to this subagent. Best-effort:
        # registry only present in FastAPI process; CLI / tests skip.
        try:
            pass

            from app.main import app as _app

            registry = getattr(_app.state, "root_abort_registry", None)
            active_run_id = self.active_parent_run_id
            if (
                registry is not None
                and active_run_id is not None
                and inbox_row.get("id")
            ):
                # The child "run_id" we want to register is the new
                # subagent's eventual agent_runs row id. We don't have
                # one yet at enqueue time — the runner allocates it
                # later. As a stand-in we register the inbox_message_id
                # so any caller with a way to map can fire later.
                # When the runner does spawn a real run, it should also
                # register that run_id against the same root.
                registry.register_child(
                    parent_run_id=active_run_id,
                    child_run_id=str(inbox_row["id"]),
                )
        except Exception:
            pass  # never break dispatch on telemetry failure

        logger.info(
            f"[delegate] {self.caller_agent_id} → {target_agent_id} "
            f"(slug={slug}, depth={self.agent_depth + 1}, "
            f"inbox={inbox_row.get('id')}, outbox={(outbox_row or {}).get('id')}, "
            f"await={await_result})"
        )

        base_response = {
            "delegated_to": slug,
            "agent_id": str(target_agent_id),
            "inbox_message_id": inbox_row.get("id"),
            "outbox_message_id": (outbox_row or {}).get("id"),
            "depth": self.agent_depth + 1,
            "await": await_result,
        }

        if not await_result:
            return {
                **base_response,
                "status": "queued",
                "note": (
                    "Task is queued. The target agent will pick it up on its "
                    "next dispatch tick. Use a status_query message or read "
                    "the outbox to track completion."
                ),
            }

        # F milestone: poll the target's task lifecycle until terminal.
        await_outcome = await self._await_delegated_result(
            caller_inbox_id=UUID(inbox_row["id"]),
            timeout_seconds=await_timeout,
        )
        return {**base_response, **await_outcome}

    # ────────────────────────────────────────────────────────────
    # Awaited delegation (F milestone)
    # ────────────────────────────────────────────────────────────

    async def _await_delegated_result(
        self,
        *,
        caller_inbox_id: UUID,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Poll the target's task until it's terminal or the timeout
        expires. Returns a dict that's spread into the tool response.

        On success: ``status='done'`` (or 'failed' / 'cancelled') with
        ``result`` (the task's content payload) and ``waited_seconds``.

        On timeout: ``status='timeout'`` with ``waited_seconds`` and a
        note pointing the caller at status_query for follow-up.

        Backoff: 1s → 1.5s → 2.25s → … capped at 5s. Cumulative budget
        is bounded by ``timeout_seconds``.
        """
        start = monotonic()
        deadline = start + timeout_seconds
        delay = 1.0
        last_task: Optional[Dict[str, Any]] = None

        while True:
            now = monotonic()
            if now >= deadline:
                break

            try:
                task = await self._lookup_task_by_inbox(caller_inbox_id)
            except Exception as err:  # pragma: no cover — defensive
                logger.warning(f"[delegate] await poll failed: {err}")
                task = None

            if task is not None:
                last_task = task
                lifecycle = task.get("lifecycle_status")
                if lifecycle in TERMINAL_LIFECYCLE_STATES:
                    waited = monotonic() - start
                    return self._format_terminal_outcome(task, waited)

            # Sleep, but never past the deadline.
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 1.5, 5.0)

        waited = monotonic() - start
        return {
            "status": "timeout",
            "waited_seconds": round(waited, 2),
            "last_lifecycle": (last_task or {}).get("lifecycle_status"),
            "note": (
                f"Awaited the target for {waited:.1f}s without a terminal "
                "lifecycle. The task is still queued/running — use a "
                "status_query message or check the outbox later."
            ),
        }

    def _format_terminal_outcome(
        self,
        task: Dict[str, Any],
        waited_seconds: float,
    ) -> Dict[str, Any]:
        """Translate a terminal task row into the awaited-response shape."""
        lifecycle = task.get("lifecycle_status")
        if lifecycle == "done":
            result = task.get("result") or {}
            content = result.get("content") if isinstance(result, dict) else None
            return {
                "status": "done",
                "waited_seconds": round(waited_seconds, 2),
                "result": content,
                "task_id": task.get("id"),
                "run_id": (result.get("run_id") if isinstance(result, dict) else None),
            }
        # failed / cancelled
        return {
            "status": lifecycle,
            "waited_seconds": round(waited_seconds, 2),
            "task_id": task.get("id"),
            "error_code": task.get("error_code"),
            "error_message": task.get("error_message"),
        }

    async def _lookup_task_by_inbox(
        self, caller_inbox_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Find the agent task that the target's inbox processor created
        for our inbox row. Returns None if the row hasn't been picked up
        yet (dispatcher still warming up) or if the lookup fails.

        A4: agent_tasks → task_tracking WHERE task_kind='agent_task'.
        Returns the agent_tasks-shape dict so callers continue reading
        ``lifecycle_status`` / ``result`` / ``error_code`` / ``error_message``
        as before."""
        try:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import TaskTracking

            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(
                                TaskTracking.dbos_workflow_id,
                                TaskTracking.phase,
                                TaskTracking.error_code,
                                TaskTracking.error_msg,
                                TaskTracking.metadata_.label("metadata"),
                            )
                            .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                            .where(
                                TaskTracking.inbox_message_id == str(caller_inbox_id)
                            )
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
            return tt_row_to_task_shape(dict(row) if row else None)
        except Exception as err:
            logger.debug(f"[delegate] task lookup transient: {err}")
            return None

    # ────────────────────────────────────────────────────────────
    # Cycle detection
    # ────────────────────────────────────────────────────────────

    # Walk depth cap. ``MAX_DELEGATION_DEPTH`` already bounds well-formed
    # chains; this is a defense-in-depth limit so a corrupt parent_run_id
    # cycle (data loop) doesn't loop the walker forever.
    MAX_CHAIN_WALK_DEPTH = 16

    async def _detect_cycle(self, *, target_agent_id: UUID):
        """Return the ancestor run id where the target agent first
        appears in the chain, or None if no cycle.

        Walk = follow agent_runs.parent_run_id starting from
        ``active_parent_run_id`` — the run executing right now, NOT the
        constructor value, which is empty on a root run and therefore used to
        skip the walk entirely. At each hop, if
        ``agent_id == target_agent_id`` we've found a cycle (target
        already running upstream). Caller's own agent is also checked
        — caller_agent_id == target_agent_id is filtered earlier as
        self-delegate, but a delegation that would re-enter ANY agent
        already on the chain is a cycle.
        """
        start = self.active_parent_run_id
        if start is None:
            # Genuinely nothing to walk: no recorder AND no inherited id, so
            # there is no chain. Distinct from "the walk found nothing".
            return None

        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRuns

        current = start
        for _ in range(self.MAX_CHAIN_WALK_DEPTH):
            if current is None:
                return None
            try:
                # agent_runs.id is BIGINT (migration 232) — bind int, not str
                # (asyncpg is strict). ``current`` is the numeric snowflake id.
                async with read_scope() as session:
                    row = (
                        (
                            await session.execute(
                                select(
                                    AgentRuns.id,
                                    AgentRuns.agent_id,
                                    AgentRuns.parent_run_id,
                                )
                                .where(AgentRuns.id == int(current))
                                .limit(1)
                            )
                        )
                        .mappings()
                        .first()
                    )
            except Exception as err:
                # Best-effort: if the lookup fails, fall through and
                # let the rest of the dispatch continue. The depth cap
                # still protects against runaway recursion.
                logger.warning(
                    f"[delegate] cycle-walk lookup failed at {current}: {err}"
                )
                return None

            data = dict(row) if row else None
            if not data:
                return None

            if str(data.get("agent_id")) == str(target_agent_id):
                return data["id"]

            parent = data.get("parent_run_id")
            current = UUID(parent) if parent else None

        # Walked the cap without resolution — treat as cycle to be safe.
        logger.warning(
            f"[delegate] cycle-walk hit MAX_CHAIN_WALK_DEPTH "
            f"({self.MAX_CHAIN_WALK_DEPTH}) — refusing dispatch"
        )
        return start
