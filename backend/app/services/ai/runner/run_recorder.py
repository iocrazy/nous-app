"""RunRecorder — context manager for persisting one agent_runs row.

Every agent invocation goes through this. AgentRunner wraps tool-resolution
loops; VisualAnalysisService wraps image-block chat completions; anything
else that hits an LLM should also wrap through here so telemetry coverage
stays complete.

Lifecycle:
    async with RunRecorder(agent_id=..., user_id=..., trigger='chat') as rec:
        # pre-flight: rec.start() ran on __aenter__, inserting the row.
        # If the agent is paused (paused_reason IS NOT NULL), __aenter__
        # raises AgentPausedError before any LLM call.

        for iteration in loop:
            if await rec.check_cancelled():
                break  # poll-based cancel (no LISTEN/NOTIFY)
            await rec.heartbeat()  # rate-limited to every 15s
            resp = await adapter.call(...)
            rec.record_usage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
            )

        rec.set_summaries(input_summary='...', output_summary='...')
        # On __aexit__ with no exception: status='completed', cost_cents computed.
        # On exception: status='failed', error_code + error_message set.
        # If check_cancelled was observed: status='cancelled'.

Design principles:
    - Telemetry failures NEVER break the agent run. Every DB write is
      try-except'd; failures log ERROR and return. The agent user sees
      a successful response even if the row didn't persist.
    - Heartbeat writes are rate-limited (15s local, 2min sweeper threshold
      means 8x headroom before a healthy run is flagged heartbeat_lost).
    - Price snapshot happens once at start — admin price edits don't
      rewrite history.
    - Cost computation happens on __aexit__, using the snapshot columns.
    - Cancel is polling-based: caller flips cancel_requested=true; the
      recorder observes it via check_cancelled(). No LISTEN/NOTIFY because
      Supabase's pgbouncer transaction pooling would drop it.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.services.ai.runner.run_projection import apply as apply_projection
from app.services.ai.runner.run_projection import empty_views, recompute_spent


class AgentPausedError(Exception):
    """Raised by RunRecorder.start() when the agent has paused_reason set.

    Contract: runner re-raises to caller. Caller surfaces a user-facing
    "agent paused" message. No agent_runs row is created — the pause is
    pre-flight, not a failed run.
    """


class AgentBusyError(AgentPausedError):
    """Raised by RunRecorder.start() when the agent already has
    max_concurrent_runs live runs (mig 286, paperclip P4).

    Subclasses AgentPausedError ON PURPOSE: every existing call site that
    surfaces a pre-flight pause ("agent paused") handles this identically
    without modification — the run is rejected before any LLM call and no
    agent_runs row is created.
    """


class RunCancelledError(Exception):
    """Raised internally when cancel_requested=true is observed.

    Recorder translates this to status='cancelled' on __aexit__. Callers
    should let it propagate; they should NOT catch-and-continue.
    """


@dataclass
class RunRecorder:
    """Context manager for one agent invocation."""

    agent_id: UUID
    user_id: UUID
    trigger: str  # 'chat' | 'script_ai' | 'visual_analysis' | 'summary' | ...

    # Optional context. ai_sessions.id is BIGINT Snowflake (mig 231) → str.
    session_id: Optional[str] = None
    conversation_id: Optional[int] = None  # Phase 1.5: structural run→conversation link
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    # mig 404 (A4): the third member of the server-bound scope triple. Callers
    # must NOT pass a model-supplied value here — derive it server-side (see
    # app/services/ai/scope/scope_binding.py::resolve_dispatch_scope, which
    # every dispatch path uses). NULL = project-wide, the correct default
    # whenever the dispatcher cannot name an episode.
    episode_id: Optional[int] = None
    issue_id: Optional[int] = None  # links this run to an issue via mig-208 triggers
    # Paperclip-style bidirectional task linkage (mig 282). When the run
    # executes inside a tracked workflow, pass the task_tracking PK
    # (dbos_workflow_id, text). The recorder then:
    #   run → task: agent_runs.task_id = task_id (insert)
    #   task → run: task_tracking.agent_id = agent_id +
    #               metadata.run_id = run id   (post-insert stamp)
    # so the agent Dashboard's task panels see service work, not just
    # chat-Delegate dispatches.
    task_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    input_summary: Optional[str] = None
    # W3c two-level cost attribution: 'direct_human' (a human initiated this
    # turn) vs 'rule_owner' (a scheduled routine / pipeline advance fired it on
    # the owner's behalf). None → treated as direct_human on finish. Set by the
    # issue-dispatch path from the issue origin_kind; interactive chat/script
    # leave it None (human).
    attribution: Optional[str] = None
    # Phase 2b-1 §2.3: set only on a forked run (mig 453 columns). The
    # original run is never written; this row points back at it.
    fork_of_run_id: Optional[int] = None
    fork_at_seq: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Internal state (populated by start / methods; not caller-facing)
    # str form of agent_runs.id (BIGINT Snowflake since mig 232). Not a UUID.
    run_id: Optional[str] = field(default=None, init=False)
    _prompt_tokens: int = field(default=0, init=False)
    _completion_tokens: int = field(default=0, init=False)
    _skill_slugs_used: list[str] = field(default_factory=list, init=False)
    _output_summary: Optional[str] = field(default=None, init=False)
    _prompt_rate: Optional[float] = field(default=None, init=False)  # cents per 1k
    _completion_rate: Optional[float] = field(default=None, init=False)
    _cached_input_tokens: int = field(default=0, init=False)
    _cached_rate: Optional[float] = field(default=None, init=False)  # cents per 1k
    _last_heartbeat_monotonic: float = field(default=0.0, init=False)
    _cancelled: bool = field(default=False, init=False)
    _event_seq: int = field(default=0, init=False)
    # seq of the last event actually PERSISTED — None when nothing was
    # recorded yet or the insert failed. Distinct from _event_seq, which
    # is the writer's counter and advances even on a failed insert.
    _last_event_seq: Optional[int] = field(default=None, init=False)
    _event_writer: Optional["RunEventWriter"] = field(
        default=None, init=False, repr=False
    )
    # Background task that keeps heartbeat_at fresh for the whole turn (not
    # just between iterations) so the liveness reaper can't false-kill a
    # healthy run stuck in one long LLM/tool call. Started on a successful
    # insert, cancelled on __aexit__.
    _heartbeat_task: Optional["asyncio.Task[Any]"] = field(default=None, init=False)

    HEARTBEAT_RATE_LIMIT_S: float = 15.0  # local, DB-write throttle
    EVENT_VALUE_MAX_CHARS: int = 4000  # per-field payload truncation

    async def _start_once(self) -> None:
        """One start attempt: pause/concurrency pre-flight → price snapshot →
        insert the agent_runs row (which sets self.run_id). _snapshot_price is
        already internally best-effort, so only pre-flight and the insert can
        raise here."""
        await self._pre_flight_check_paused()
        await self._snapshot_price()
        await self._insert_row()

    async def __aenter__(self) -> "RunRecorder":
        try:
            await self._start_once()
        except AgentPausedError:
            # Re-raise — caller needs to surface the pause to the user,
            # and we intentionally do NOT persist a run row for pre-flight rejections.
            raise
        except Exception as err:
            # Retry ONCE on a transient failure (pgbouncer recycle, brief
            # network blip). Without a persisted row self.run_id stays None,
            # which silently disables BOTH telemetry AND cancellation for the
            # whole run — a single transient blip shouldn't cost that. A pause
            # surfacing on the retry still propagates.
            logger.warning(f"[RunRecorder] start failed, retrying once: {err}")
            try:
                await asyncio.sleep(0.1)
                await self._start_once()
            except AgentPausedError:
                raise
            except Exception as err2:
                # Both attempts failed — degrade gracefully (no row, no
                # telemetry/cancel), never break the agent run.
                logger.error(
                    "[RunRecorder] start retry also failed "
                    f"(telemetry disabled for this run): {err2}"
                )
        # Keep heartbeat_at fresh for the WHOLE turn — a single >2min LLM/tool
        # call would otherwise let the run go silent between iterations and the
        # liveness reaper would falsely mark a healthy run dead (acute on a
        # multi-pod deploy: one pod's restart-reconcile reaps another pod's
        # live runs). Only armed when the insert succeeded (run_id set).
        if self.run_id is not None:
            self._start_background_heartbeat()
        return self

    def _start_background_heartbeat(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync test path) — between-iteration heartbeat only
        self._heartbeat_task = loop.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Refresh heartbeat every HEARTBEAT_RATE_LIMIT_S until cancelled.
        Delegates to ``heartbeat()`` so the rate-limit guard dedupes against
        between-iteration calls. Cancelled cleanly in __aexit__; if the
        process dies, the task dies with it and the run correctly goes stale."""
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_RATE_LIMIT_S)
                await self.heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a heartbeat blip must not crash the run
            logger.warning("[RunRecorder] background heartbeat stopped", exc_info=True)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        # Stop the background heartbeat first so it can't race _finish (which
        # flips status away from 'running').
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._heartbeat_task = None

        if self.run_id is None:
            # Start failed; nothing to finalize.
            return False

        final_status = "completed"
        final_error: Optional[str] = None
        try:
            if self._cancelled:
                final_status = "cancelled"
                await self._finish(status="cancelled")
            elif exc is None:
                await self._finish(status="completed")
            else:
                final_status = "failed"
                error_code = exc_type.__name__ if exc_type else "unknown"
                final_error = str(exc) if exc else None
                await self._finish(
                    status="failed", error_code=error_code, error_message=final_error
                )
        except Exception as err:
            logger.error(f"[RunRecorder] finish failed for run {self.run_id}: {err}")

        self._maybe_export_langfuse(status=final_status, error_message=final_error)

        # Never swallow user exceptions — propagate them out.
        return False

    def _maybe_export_langfuse(
        self, *, status: str, error_message: Optional[str]
    ) -> None:
        """Phase 4.5-6: fire-and-forget trace export to the self-hosted
        Langfuse. Gated inside the exporter (FEATURE_LANGFUSE, default
        off) — when inoperative this is one cheap config check. Never
        raises; a Langfuse outage only costs the trace."""
        try:
            from app.services.ai.telemetry.langfuse_exporter import (
                get_langfuse_exporter,
            )

            exporter = get_langfuse_exporter()
            if not exporter.config.operative():
                return
            import asyncio

            asyncio.get_running_loop().create_task(
                exporter.export_run(
                    run_id=str(self.run_id),
                    agent_slug="",  # recorder holds agent_id, not slug
                    status=status,
                    trigger=self.trigger,
                    user_id=str(self.user_id),
                    session_id=self.session_id,
                    model=self.model,
                    provider=self.provider,
                    input_summary=self.input_summary,
                    output_summary=self._output_summary,
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cost_cents=self.compute_cost_cents(),
                    error_message=error_message,
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("[RunRecorder] langfuse export skipped", exc_info=True)

    # -------- public API for callers inside the `async with` block -------

    def record_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        """Accumulate token counts. Safe to call many times per run.

        ``cached_input_tokens`` is the subset of ``prompt_tokens`` served from
        the provider's prompt cache (billed cheaper when a cached rate exists).
        """
        self._prompt_tokens += max(0, prompt_tokens or 0)
        self._completion_tokens += max(0, completion_tokens or 0)
        self._cached_input_tokens += max(0, cached_input_tokens or 0)

    def compute_cost_cents(self) -> float:
        """Cost so far in cents. Cached tokens use ``_cached_rate`` when set,
        else the prompt rate (= legacy behavior, no billing regression).
        Returns 0.0 when rates are unknown (UI shows '—')."""
        if self._prompt_rate is None or self._completion_rate is None:
            return 0.0
        cached = min(self._cached_input_tokens, self._prompt_tokens)
        billable_prompt = self._prompt_tokens - cached
        cached_rate = (
            self._cached_rate if self._cached_rate is not None else self._prompt_rate
        )
        return (
            billable_prompt / 1000.0 * self._prompt_rate
            + cached / 1000.0 * cached_rate
            + self._completion_tokens / 1000.0 * self._completion_rate
        )

    @property
    def prompt_tokens(self) -> int:
        """Accumulated prompt tokens seen on this run so far."""
        return self._prompt_tokens

    @property
    def completion_tokens(self) -> int:
        """Accumulated completion tokens seen on this run so far."""
        return self._completion_tokens

    def record_skill(self, slug: str) -> None:
        """Track which skills got invoked during this run."""
        if slug and slug not in self._skill_slugs_used:
            self._skill_slugs_used.append(slug)

    def note_compaction(self, stats: Any) -> None:
        """Phase 5 of #199: accumulate context-compactor counters into
        metadata so the Runs UI can show how often a long run hit the
        compactor and how many tokens that bought back.

        Tolerant of any object exposing ``tier`` + ``tokens_saved``
        attributes (typically ``app.agent_framework.CompactionStats``)
        — duck-typing keeps this layer free of an import cycle into
        agent_framework.
        """
        try:
            tier = getattr(stats, "tier", None)
            saved = int(getattr(stats, "tokens_saved", 0) or 0)
            tier_str = getattr(tier, "value", None) or str(tier or "")
        except Exception:  # noqa: BLE001 — never break a run over telemetry
            return
        used = getattr(stats, "tokens_before", None)
        window = getattr(stats, "window", None)
        if isinstance(used, int) and isinstance(window, int) and window > 0:
            self.measure_context(used, window)
        if not tier_str or saved <= 0:
            return
        # metadata.compaction.{tier_str}_count + total_tokens_saved
        comp = self.metadata.setdefault("compaction", {})
        comp[f"{tier_str}_count"] = int(comp.get(f"{tier_str}_count", 0)) + 1
        comp["total_tokens_saved"] = int(comp.get("total_tokens_saved", 0)) + saved

    def note_subagent(self, envelope: dict[str, Any]) -> None:
        """Phase 5 of #199: count sub-agent spawns + their cost so the
        parent run's metadata exposes the tree at a glance.

        Reads ``envelope.tokens_used`` and ``envelope.status`` defensively
        — a malformed envelope (any future schema drift) increments the
        count without polluting totals.
        """
        sub = self.metadata.setdefault("subagents", {})
        sub["count"] = int(sub.get("count", 0)) + 1
        try:
            tokens = int((envelope or {}).get("tokens_used") or 0)
            sub["tokens_used"] = int(sub.get("tokens_used", 0)) + tokens
        except (TypeError, ValueError):
            pass
        status = (envelope or {}).get("status")
        if status == "failed":
            sub["failed_count"] = int(sub.get("failed_count", 0)) + 1

    def set_summaries(
        self,
        *,
        input_summary: Optional[str] = None,
        output_summary: Optional[str] = None,
    ) -> None:
        """Set display-only summaries. Full content belongs in metadata['full_input'] / 'full_output']."""
        if input_summary is not None:
            self.input_summary = _truncate(input_summary, 500)
        if output_summary is not None:
            self._output_summary = _truncate(output_summary, 500)

    async def record_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn: Optional[int] = None,
        step: Optional[int] = None,
    ) -> None:
        """Append one transcript event and refresh the folded views.

        Everything goes through ``RunEventWriter`` — the same write path the
        sweeper uses for the runs it closes after the fact — so there is one
        insert, one fold, one mirror. Best-effort: a failed write never
        breaks the run. ``turn`` / ``step`` are the mig-453 coordinates.
        """
        if self.run_id is None:
            return
        self._last_event_seq = await self._writer().append(
            event_type, payload, turn=turn, step=step
        )
        self._event_seq = self._writer().seq

    def _writer(self) -> "RunEventWriter":
        if self._event_writer is None:
            self._event_writer = RunEventWriter(
                int(self.run_id),
                seq_start=self._event_seq,
                value_max_chars=self.EVENT_VALUE_MAX_CHARS,
            )
        return self._event_writer

    @property
    def last_event_seq(self) -> Optional[int]:
        """The seq the LAST ``record_event`` actually wrote — ``None`` when
        this recorder recorded nothing or the insert failed. A caller that
        must point back at its own event reads it right after the call (the
        deliverable registry stamps it onto ``run_deliverables.seq``).

        Deliberately not ``next_event_seq - 1``: that counter advances even
        when the insert failed, so it would name an event no transcript has.
        """
        return self._last_event_seq

    @property
    def next_event_seq(self) -> Optional[int]:
        """The seq the next ``record_event`` will take — what a caller needs
        to mint an id that names its own event (``question_id`` = ``q:<run>:<seq>``).
        ``None`` when this recorder records nothing (no run row)."""
        if self.run_id is None:
            return None
        return self._writer().seq + 1

    @property
    def views(self) -> dict[str, Any]:
        """Current folded views (``view`` / ``cost``) for this run."""
        return self._writer().views if self.run_id is not None else empty_views()

    def measure_context(self, used: int, window: int) -> None:
        """Feed the context gauge from a local measurement (no event row):
        green/yellow compaction tiers emit nothing, yet the gauge must move."""
        if self.run_id is None:
            return
        self._writer().fold_local("context_measured", {"used": used, "window": window})

    def cost_of(self, prompt: int, completion: int, cached: int = 0) -> Optional[float]:
        """Cents for one call at this run's rates; None when rates are unknown."""
        if self._prompt_rate is None or self._completion_rate is None:
            return None
        cached_rate = (
            self._cached_rate if self._cached_rate is not None else self._prompt_rate
        )
        billable = max(0, prompt - cached)
        return round(
            billable / 1000.0 * self._prompt_rate
            + cached / 1000.0 * cached_rate
            + completion / 1000.0 * self._completion_rate,
            6,
        )

    async def heartbeat(self) -> None:
        """Refresh heartbeat_at if >=15s since last write.

        Piggybacks a cancel check — the DB round-trip already happens.
        """
        if self.run_id is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_monotonic < self.HEARTBEAT_RATE_LIMIT_S:
            return
        self._last_heartbeat_monotonic = now
        try:
            from datetime import datetime, timezone

            from sqlalchemy import update as sa_update

            from app.db.session import write_scope
            from app.models import AgentRuns

            # agent_runs.id is BIGINT (mig 232) → int; "now()" sentinel → native
            # datetime (asyncpg rejects the literal string).
            async with write_scope() as session:
                await session.execute(
                    sa_update(AgentRuns)
                    .where(AgentRuns.id == int(self.run_id))
                    .where(AgentRuns.status == "running")
                    .values(heartbeat_at=datetime.now(timezone.utc))
                )
        except Exception as err:
            logger.warning(f"[RunRecorder] heartbeat failed for {self.run_id}: {err}")

    async def check_cancelled(self) -> bool:
        """Poll cancel_requested. Runner should break out of its loop when true."""
        if self.run_id is None:
            return False
        try:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import AgentRuns

            async with read_scope() as session:
                row = (
                    await session.execute(
                        select(AgentRuns.cancel_requested)
                        .where(AgentRuns.id == int(self.run_id))
                        .limit(1)
                    )
                ).first()
            if row is not None and row[0]:
                self._cancelled = True
                return True
        except Exception as err:
            logger.warning(
                f"[RunRecorder] cancel check failed for {self.run_id}: {err}"
            )
        return False

    async def check_paused(self) -> bool:
        """Poll ``pause_requested`` (phase 2a target-level pause). Same
        shape as ``check_cancelled``: one SELECT per step boundary, a failed
        read is "not paused" (the pause is retried at the next boundary; a
        telemetry blip must never stall a healthy run). Unlike cancel it does
        NOT flip the finish status — the run ends ``completed`` and the
        ``turn_end{reason: paused}`` event is the record."""
        if self.run_id is None:
            return False
        try:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import AgentRuns

            async with read_scope() as session:
                row = (
                    await session.execute(
                        select(AgentRuns.pause_requested)
                        .where(AgentRuns.id == int(self.run_id))
                        .limit(1)
                    )
                ).first()
            return bool(row is not None and row[0])
        except Exception as err:
            logger.warning(f"[RunRecorder] pause check failed for {self.run_id}: {err}")
        return False

    # -------- internal ---------------------------------------------------

    async def _pre_flight_check_paused(self) -> None:
        # ai_agents.max_concurrent_runs (mig 286) is not on the AiAgents ORM
        # model → referenced via column(); paused_reason is mapped.
        from sqlalchemy import column, func, select

        from app.db.session import read_scope
        from app.models import AgentRuns, AiAgents

        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(AiAgents.paused_reason, column("max_concurrent_runs"))
                        .select_from(AiAgents)
                        .where(AiAgents.id == str(self.agent_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        if row and row.get("paused_reason"):
            reason = row["paused_reason"]
            raise AgentPausedError(f"agent paused (reason={reason})")

        # mig 286 (paperclip P4): per-agent concurrency cap. Counted across
        # ALL users — the limit protects the agent/provider, not one caller.
        # Race window between count and insert is accepted (paperclip's is
        # too): the cap is a throttle, not a mutex.
        limit = row.get("max_concurrent_runs") if row else None
        if limit:
            async with read_scope() as session:
                running = (
                    await session.execute(
                        select(func.count())
                        .select_from(AgentRuns)
                        .where(AgentRuns.agent_id == str(self.agent_id))
                        .where(AgentRuns.status == "running")
                    )
                ).scalar() or 0
            if running >= int(limit):
                raise AgentBusyError(
                    f"agent at max_concurrent_runs ({running}/{limit}) — "
                    "try again when a run finishes"
                )

    async def _snapshot_price(self) -> None:
        """Look up the most-recent ai_model_prices row as-of now for this model.

        Caches rates on self for use in _finish(). Silent no-op if no row
        is found — cost_cents stays NULL and UI shows '—'.
        """
        if not self.model:
            return
        try:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import AiModelPrices

            stmt = select(
                AiModelPrices.prompt_cents_per_1k,
                AiModelPrices.completion_cents_per_1k,
                AiModelPrices.cached_input_cents_per_1k,
                AiModelPrices.effective_at,
            ).where(AiModelPrices.model == self.model)
            if self.provider:
                stmt = stmt.where(AiModelPrices.provider == self.provider)
            stmt = stmt.order_by(AiModelPrices.effective_at.desc()).limit(1)
            async with read_scope() as session:
                row = (await session.execute(stmt)).mappings().first()
            if row:
                self._prompt_rate = float(row["prompt_cents_per_1k"])
                self._completion_rate = float(row["completion_cents_per_1k"])
                cr = row.get("cached_input_cents_per_1k")
                self._cached_rate = float(cr) if cr is not None else None
        except Exception as err:
            logger.warning(
                f"[RunRecorder] price snapshot lookup failed "
                f"(model={self.model} provider={self.provider}): {err}"
            )

    async def _insert_row(self) -> None:
        # task_id (mig 282, TEXT) and conversation_id (mig 331, BIGINT) are NOT
        # on the AgentRuns ORM model → build the INSERT via raw text() so both
        # drift columns are set in the same statement the legacy path used.
        # asyncpg strict binds: BIGINT cols → int, uuid cols accept str, jsonb
        # via CAST(:x AS jsonb) with a json.dumps'd value.
        from sqlalchemy import text

        from app.db.session import write_scope

        payload: dict[str, Any] = {
            "agent_id": str(self.agent_id),
            "user_id": str(self.user_id),
            "session_id": int(self.session_id) if self.session_id else None,
            "conversation_id": (
                int(self.conversation_id) if self.conversation_id else None
            ),
            "team_id": int(self.team_id) if self.team_id is not None else None,
            "project_id": int(self.project_id) if self.project_id is not None else None,
            "episode_id": int(self.episode_id) if self.episode_id is not None else None,
            "task_id": self.task_id,
            "status": "running",
            "trigger": self.trigger,
            "model": self.model,
            "provider": self.provider,
            "input_summary": self.input_summary,
            "prompt_cents_per_1k_snapshot": self._prompt_rate,
            "completion_cents_per_1k_snapshot": self._completion_rate,
            "metadata_json": self.metadata or {},
            # phase 2b-1: None → dropped by the filter below (ordinary runs)
            "fork_of_run_id": self.fork_of_run_id,
            "fork_at_seq": self.fork_at_seq,
        }
        # Drop None values so DB defaults (e.g., now()) apply.
        payload = {k: v for k, v in payload.items() if v is not None}
        if self.issue_id is not None:
            payload["issue_id"] = int(self.issue_id)

        cols = list(payload.keys())
        placeholders = [
            f"CAST(:{c} AS jsonb)" if c == "metadata_json" else f":{c}" for c in cols
        ]
        params = {
            k: (json.dumps(v) if k == "metadata_json" else v)
            for k, v in payload.items()
        }
        stmt = text(
            f"INSERT INTO agent_runs ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING id"
        )
        async with write_scope() as session:
            row = (await session.execute(stmt, params)).first()
        if row is not None:
            # agent_runs.id became a BIGINT Snowflake in migration 232 (was
            # UUID at #57). Keep run_id as the string form of whatever the DB
            # returned — every consumer only str()s it (the .eq("id", …)
            # filters and reconcile_run). Wrapping in UUID() raised ValueError
            # on the bigint, which __aenter__ swallowed as "telemetry disabled"
            # → no agent_runs row was finalised for any run after mig 232.
            self.run_id = str(row[0])
            self._last_heartbeat_monotonic = time.monotonic()
            await self._link_task()

    async def _link_task(self) -> None:
        """Stamp the task → run/agent backlink on task_tracking (mig 282).

        Sets task_tracking.agent_id (business column — phase/status/progress
        stay trigger-owned per the task-system discipline) and merges
        metadata.run_id (MERGE, never replace — the user_settings clobber
        lesson applies to every shared jsonb column). Best-effort: linkage
        failure never breaks the run.
        """
        if not self.task_id or self.run_id is None:
            return
        try:
            from sqlalchemy import select
            from sqlalchemy import update as sa_update

            from app.db.session import read_scope, write_scope
            from app.models import TaskTracking

            async with read_scope() as session:
                current = (
                    (
                        await session.execute(
                            select(TaskTracking.metadata_.label("metadata"))
                            .where(TaskTracking.dbos_workflow_id == self.task_id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
            if current is None:
                return
            merged = dict(current.get("metadata") or {})
            merged["run_id"] = self.run_id
            # agent_id + metadata are business-decoration columns (route-C rule
            # 3) — safe to PATCH; phase/status/progress stay trigger-owned.
            async with write_scope() as session:
                await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == self.task_id)
                    .values(agent_id=str(self.agent_id), metadata_=merged)
                )
        except Exception as err:
            logger.warning(
                f"[RunRecorder] task linkage failed "
                f"(task_id={self.task_id} run_id={self.run_id}): {err}"
            )

    async def _finish(
        self,
        *,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        from datetime import datetime, timezone

        from sqlalchemy import update as sa_update

        from app.db.session import write_scope
        from app.models import AgentRuns

        # Review I3. ``compute_cost_cents`` only ever knew THIS run's tokens,
        # while ``metadata_json.cost.spent_cents`` also carries what its
        # sub-agents spent. ``issue_rollup._run_cents`` reads the view while a
        # run is running and this column once it has ended, so storing the
        # own-only figure made an issue's spend DROP by the children's cost at
        # the moment the parent completed — a spend gauge walking backwards,
        # feeding the budget gate.
        #
        # Both sides are now derived from the SAME own figure: the token-based
        # one when rates are known, else whatever the step folds accumulated
        # (a run that emits no step_end events has only the former; one whose
        # model has no price row has only the latter).
        cost_cents: Optional[float] = None
        # Re-read the externally-written slices first: a background child that
        # finished mid-run put its ``subagent_done`` in the transcript, never
        # in these in-memory views, and this is the last chance to bill it.
        if self._event_writer is not None:
            await self._event_writer.refold_external_slices()
        folded = self._event_writer.views["cost"] if self._event_writer else None
        own_cents: Optional[float] = None
        if self._prompt_rate is not None and self._completion_rate is not None:
            own_cents = self.compute_cost_cents()
        elif folded is not None and folded.get("own_cents"):
            own_cents = float(folded["own_cents"])
        children_cents = sum(
            float(v or 0) for v in ((folded or {}).get("by_child") or {}).values()
        )
        if own_cents is not None or children_cents:
            cost_cents = round((own_cents or 0.0) + children_cents, 4)
        if folded is not None and own_cents is not None:
            folded["own_cents"] = round(own_cents, 4)
            recompute_spent(folded)

        # W3c: classify every finished run. None → direct_human (a human turn);
        # the issue-dispatch path sets rule_owner for routine/pipeline fires.
        effective_attribution = self.attribution or "direct_human"

        # "now()" sentinel → native datetime for the asyncpg timestamptz bind.
        #
        # A2 review note: this dict feeds `.values(**updates)` below, which a
        # static grep can't reliably follow (the keys are Python dict literals,
        # not inline kwargs at the call site). DO NOT add "project_id",
        # "team_id" or "episode_id" (mig 404) to this dict, ever —
        # app/services/ai/scope/agent_run_scope.py
        # (AgentRunScope / scope_for_run) depends on those three columns being
        # stamped exactly once at INSERT (`_insert_row` above) and never
        # touched again; every screenwriting-tool authorization decision
        # (scope_resolver.py) assumes that invariant holds. If a run's scope
        # ever needs to change after dispatch, that is a deliberate design
        # change to agent_run_scope.py, not a one-line addition here.
        updates: dict[str, Any] = {
            "status": status,
            "ended_at": datetime.now(timezone.utc),
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "cached_input_tokens": self._cached_input_tokens,
            "skill_slugs_used": self._skill_slugs_used,
            "attribution": effective_attribution,
        }
        # Terminal liveness (mig 406). Without this every finished run sat at
        # liveness_state='running' forever — this is the only writer on the
        # ordinary exit path, so nothing else ever closed the column out.
        #
        # `finished` means "the run wound up in an orderly way", NOT "the run
        # succeeded" — liveness is orthogonal to status (mig 207), so the
        # failed path gets it too: a body that raised still returned control
        # here and closed its own row. A `failed` row left on 'running' is
        # exactly as wrong as a `completed` one.
        #
        # What must NEVER be written here is 'dead'. dead ⟺ failed is a
        # whole-DB invariant: both writers of dead (liveness_scanner._mark_dead
        # and services/liveness/reconcile) set status='failed' in the SAME
        # statement, and dead means "the process actually died" — a different
        # ops playbook from "the agent raised". The agent fault badge reads
        # liveness_state IN ('stuck','dead'), so reusing dead for ordinary
        # failures would light up every agent.
        if status in ("completed", "failed"):
            updates["liveness_state"] = "finished"
        elif status == "cancelled":
            updates["liveness_state"] = "cancelled"
        if cost_cents is not None:
            updates["cost_cents"] = cost_cents
        if self._output_summary is not None:
            updates["output_summary"] = self._output_summary
        if error_code is not None:
            updates["error_code"] = error_code
        if error_message is not None:
            updates["error_message"] = error_message

        async with write_scope() as session:
            await session.execute(
                sa_update(AgentRuns)
                .where(AgentRuns.id == int(self.run_id))
                .where(AgentRuns.status == "running")  # idempotent guard
                .values(**updates)
            )

        # W3c: accumulate this turn into the ai_usage_hourly rollup the Usage
        # panel reads. Fire-and-forget (record_usage swallows internally) and
        # only when tokens were actually burned, so we don't create empty
        # rollup buckets for pre-flight rejects. module = the run trigger.
        if (self._prompt_tokens + self._completion_tokens) > 0:
            try:
                from app.services.ai_usage import record_usage

                await record_usage(
                    module=self.trigger,
                    attribution=effective_attribution,
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cached_input_tokens=self._cached_input_tokens,
                    team_id=self.team_id,
                    project_id=self.project_id,
                    agent_id=self.agent_id,
                    model=self.model,
                    cost_cents=cost_cents,
                )
            except Exception as exc:  # noqa: BLE001 — defence in depth
                logger.warning(f"[RunRecorder] usage rollup failed (non-fatal): {exc}")

        # Phase 3 Token Billing: reconcile usage on terminal status only.
        # Failure here is logged but never raised — billing must not be
        # able to roll back a finished agent_runs row.
        if status == "completed" and cost_cents is not None and cost_cents > 0:
            try:
                from app.services.ai.billing.token_billing import reconcile_run

                # cost_cents is the cents amount; PointsService treats
                # cost_points as the same scalar (1 cent ≈ 1 point in
                # the current billing model). If a future change splits
                # them, this conversion happens here.
                await reconcile_run(
                    run_id=self.run_id,
                    user_id=self.user_id,
                    team_id=self.team_id,
                    project_id=self.project_id,
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    model=self.model or "?",
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cost_points=float(cost_cents),
                    byo_key=False,  # platform-model run; BYO-key runs
                    # set this true via a future RunRecorder kwarg or
                    # by inspecting the model string against the user's
                    # registered keys (Phase 3.1 work)
                    action=self.trigger,
                )
            except Exception as exc:
                logger.warning(f"[RunRecorder] reconcile_run failed (non-fatal): {exc}")


def _truncate(text: str, max_chars: int) -> str:
    """Codepoint-safe truncation. Full content belongs in metadata_json."""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _truncate_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """JSON-safe copy of an event payload with long values truncated.

    Strings are cut at ``max_chars``. Nested dicts/lists (``usage``, ``counts``,
    ``todos``, tool ``args``/``result``) are measured by their serialized size
    and **kept as structure** when they fit — the log is the replay source
    (mig 453 spine ①), and a fold given ``"{\"total\": 3}"`` where it expects a
    dict silently returns nothing. Only an oversized nested value degrades to
    its truncated JSON string, so a deep tool result still can't sneak
    megabytes past the cap. Until 2026-09-06 every nested value was
    stringified unconditionally; the frontend folds grew a JSON.parse
    fallback for those rows and keep it for the stored history.
    """
    import json as _json

    out: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if isinstance(v, str):
            out[k] = _truncate(v, max_chars)
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            try:
                dumped = _json.dumps(v, ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001 — telemetry only
                out[k] = _truncate(repr(v), max_chars)
                continue
            if len(dumped) <= max_chars:
                # round-trip: JSON-safe copy (datetimes etc. already str'd)
                out[k] = _json.loads(dumped)
            else:
                out[k] = _truncate(dumped, max_chars)
    return out


async def event_writer_for_run(run_id: Any) -> "RunEventWriter":
    """Module-level entry for the late-append case (answering a parked
    question, FinishIssue options → question_asked). Callers import it under
    a private name so their tests can patch the seam per module."""
    return await RunEventWriter.for_run(run_id)


#: The two event types a run's children are folded from. Both can be written
#: by the background worker on a run it does not own, which is why they are
#: re-read from the transcript rather than trusted from memory.
SUBAGENT_EVENT_TYPES = ("subagent_spawned", "subagent_done")

#: Every event type a SECOND writer can put on a run's transcript, hence every
#: view slice the mirror has to re-read instead of trusting memory.
#: ``deliverable`` joins the two sub-agent types for the same reason: the
#: deliverables registry falls back to ``RunEventWriter.for_run`` whenever no
#: recorder is handed to it (三期 3a T8c 缺陷 1 — the folded ``view.outputs``
#: was mirrored by that second writer and wiped by the live recorder's next
#: whole-value mirror, so the cockpit's outputs cell could never render).
EXTERNALLY_WRITTEN_EVENT_TYPES = (*SUBAGENT_EVENT_TYPES, "deliverable")

#: Postgres ``unique_violation``. The ONLY IntegrityError that means "another
#: writer took my seq" — the same table also carries an ``event_type`` CHECK
#: allowlist, a ``run_id`` FK and three NOT NULLs, and each of those is an
#: IntegrityError too. Treating one of THOSE as a race would log three "another
#: writer is on this run" warnings that send the next reader hunting for a
#: second writer that does not exist, pay two doomed retries, and latch
#: ``foreign_writer_seen`` on for the rest of the run.
_UNIQUE_VIOLATION_SQLSTATE = "23505"

#: The unique index the retry is about. asyncpg puts the violated constraint's
#: name on the original error; when it is present and names something else, the
#: conflict is not ours. Absent (another driver, a wrapped error) it is not
#: treated as disqualifying — the sqlstate above is the load-bearing check.
_SEQ_UNIQUE_CONSTRAINT = "agent_run_transcript_events_run_id_seq_key"

#: How many times ``append`` re-seeds its counter and retries after losing the
#: ``(run_id, seq)`` race. Bounded because the alternative failure — a genuine
#: constraint problem that looks like a conflict — must not spin: three tries
#: cover a second writer landing one or two events between our read and our
#: insert, and anything beyond that is a different bug that should surface as
#: the ordinary "insert failed" warning.
_SEQ_CONFLICT_ATTEMPTS = 3


def _is_seq_conflict(err: IntegrityError) -> bool:
    """True only for ``UNIQUE (run_id, seq)`` — "someone else took my number".

    Read off the DRIVER's original error, not the message text: asyncpg exposes
    ``sqlstate``, psycopg ``pgcode``, and both name the violated constraint.
    Every other IntegrityError this table can raise (the ``event_type`` CHECK
    allowlist, the ``run_id`` FK, three NOT NULLs) is a wiring bug, and a wiring
    bug diagnosed as a race is worse than one diagnosed as nothing: the log then
    names a second writer that does not exist.

    The sqlstate is the load-bearing check. The constraint name only ever
    DISQUALIFIES — when the driver supplies one and it is a different index. A
    driver that supplies none leaves the decision to the sqlstate.
    """
    orig = getattr(err, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate != _UNIQUE_VIOLATION_SQLSTATE:
        return False
    name = getattr(orig, "constraint_name", None)
    return name is None or name == _SEQ_UNIQUE_CONSTRAINT


class RunEventWriter:
    """The one write path onto a run's transcript + its folded views.

    ``append`` = insert the event row (ORM, mig-453 coordinates) → fold it
    into ``views`` → mirror the whole ``view`` / ``cost`` values into
    ``agent_runs.metadata_json``. Used by ``RunRecorder`` for live runs and
    by the sweeper for runs it closes after the fact (``turn_end
    interrupted``) — so there is exactly one way an event reaches storage.

    The mirror is skipped when a fold returned the same object (nothing
    changed). Legacy keys ``todos`` / ``turn_end_reason`` / ``last_retry``
    are still written from the view during the phase-1 transition; Task 7
    removes them once the frontend reads ``view``.
    """

    def __init__(self, run_id: int, *, seq_start: int = 0, value_max_chars: int = 4000):
        self.run_id = int(run_id)
        self.seq = seq_start
        self.value_max_chars = value_max_chars
        self.views: dict[str, Any] = empty_views()
        self._pending_mirror = False
        # Sub-agent events this writer folded but could NOT persist (the
        # insert-failure branch below). ``refold_external_slices`` rebuilds
        # those two slices from the transcript, so anything missing from it
        # has to be replayed on top or it would be dropped on the next mirror.
        # Edge: an insert that raised may still have COMMITTED (an ambiguous
        # failure — the connection dropped after the write). The event is then
        # both in the transcript and here, so the counters inflate by one;
        # ``by_child`` is keyed and stays exact, which is the half that bills.
        self._unpersisted_subagent: list[tuple[str, dict[str, Any]]] = []
        # True once an insert lost the ``(run_id, seq)`` race — proof that a
        # SECOND writer is on this run. It is the only such signal available in
        # memory, and ``refold_external_slices`` uses it to open its guard: a
        # run whose outputs are ALL written by the other writer has nothing else
        # to go on (T8c 修复轮 1).
        self._seq_conflicts = 0

    @property
    def foreign_writer_seen(self) -> bool:
        """Another writer has taken a seq on this run during this writer's life."""
        return self._seq_conflicts > 0

    @classmethod
    async def for_run(cls, run_id: Any) -> "RunEventWriter":
        """A writer for a run this process does not own: the deliverables
        registry, workforce's ``subagent_done``, a parked question being
        answered, FinishIssue options → ``question_asked``.

        ⚠️ **The run may still be RUNNING.** ``GenerateShotImage`` dispatches to
        DBOS and the parent keeps iterating, so its ``register_generated_media``
        can land while the parent's own recorder is live — two writers, one run.
        Both halves of that are handled: ``append`` re-seeds and retries on the
        unique-index collision, and ``refold_external_slices`` rebuilds the
        slices either writer can touch.

        Continues the run's seq and folds onto its STORED views — a fresh writer
        would mirror an empty projection over ``metadata_json.view`` and erase
        the run's history."""
        from sqlalchemy import func, select

        from app.db import session as _dbs
        from app.models.agents import AgentRuns, AgentRunTranscriptEvents

        rid = int(run_id)
        async with _dbs.read_scope() as session:
            max_seq = (
                await session.execute(
                    select(func.max(AgentRunTranscriptEvents.seq)).where(
                        AgentRunTranscriptEvents.run_id == rid
                    )
                )
            ).scalar()
            meta = (
                await session.execute(
                    select(AgentRuns.metadata_json).where(AgentRuns.id == rid)
                )
            ).scalar_one_or_none()
        writer = cls(rid, seq_start=int(max_seq or 0))
        if isinstance(meta, dict):
            for key in ("view", "cost"):
                stored = meta.get(key)
                if not isinstance(stored, dict):
                    continue
                if key == "cost" and "own_cents" not in stored:
                    # Recorded before ``own_cents`` existed. No fold could add
                    # a child to that total then, so it WAS this run's own —
                    # seeding 0 instead would let the next late subagent_done
                    # recompute the column down to children-only.
                    stored = {
                        **stored,
                        "own_cents": float(stored.get("spent_cents") or 0.0),
                    }
                writer.views[key] = {**writer.views[key], **stored}
        return writer

    async def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn: Optional[int] = None,
        step: Optional[int] = None,
    ) -> Optional[int]:
        """Insert the row, fold, mirror. Returns the seq, or None when the
        insert failed (folded locally, not mirrored — see below).

        **Seq collisions are retried, not lost** (T8c 修复轮 1). Two writers
        can be on one run at once — the live recorder and anything going
        through ``for_run`` (the deliverables registry, workforce, a parked
        question) — and each holds its own counter. ``for_run`` seeds from
        ``max(seq)``, so whichever writer moves second asks for a seq the other
        already took and hits ``UNIQUE (run_id, seq)`` (mig 397). Before this,
        that event was gone: the failure branch below logged a warning and
        returned, and only ``SUBAGENT_EVENT_TYPES`` were replayed, so a
        ``tool_call`` / ``assistant`` / ``deliverable`` simply vanished from the
        transcript. 真栈 run 348401200407189 (``deliverable(4) tool_call(5)``)
        shows exactly that — the gapless numbering is the evidence of the
        swallowed row, not evidence that nothing happened.

        So on a conflict the counter is RE-SEEDED from the database and the
        insert retried, bounded by ``_SEQ_CONFLICT_ATTEMPTS``. Both writers get
        this, because both go through here.
        """
        for attempt in range(1, _SEQ_CONFLICT_ATTEMPTS + 1):
            self.seq += 1
            seq = self.seq
            try:
                from sqlalchemy import insert

                from app.db.session import write_scope
                from app.models import AgentRunTranscriptEvents

                async with write_scope() as session:
                    await session.execute(
                        insert(AgentRunTranscriptEvents).values(
                            run_id=self.run_id,
                            seq=seq,
                            event_type=event_type,
                            payload=_truncate_payload(payload, self.value_max_chars),
                            turn=turn,
                            step=step,
                        )
                    )
            except IntegrityError as err:
                if not _is_seq_conflict(err):
                    # A different constraint — a wiring bug, not a race. Same
                    # path as any other failed insert, and deliberately NOT
                    # counted as a foreign writer: that flag latches the refold
                    # guard open for the rest of the run.
                    logger.warning(
                        f"[RunEventWriter] insert rejected (run={self.run_id} "
                        f"seq={seq} type={event_type}): {err}"
                    )
                    return self._insert_failed(event_type, payload, seq)
                # Someone else is writing this run. Say so — both that it
                # happened and which event nearly went missing — then re-seed
                # and try again. The re-seed READ is the whole point: our
                # counter is stale by however many seqs the other writer took.
                self._seq_conflicts += 1
                logger.warning(
                    f"[RunEventWriter] seq {seq} already taken on run "
                    f"{self.run_id} (type={event_type}, attempt {attempt}/"
                    f"{_SEQ_CONFLICT_ATTEMPTS}) — another writer is on this "
                    f"run; re-seeding from the transcript: {err}"
                )
                if attempt == _SEQ_CONFLICT_ATTEMPTS:
                    return self._insert_failed(event_type, payload, seq)
                reseeded = await self._max_persisted_seq()
                if reseeded is None:
                    # Cannot re-seed (the read failed). Retrying with the same
                    # stale counter would only collide again.
                    return self._insert_failed(event_type, payload, seq)
                self.seq = reseeded
                continue
            except Exception as err:  # noqa: BLE001 — telemetry never fails a run
                logger.warning(
                    f"[RunEventWriter] insert failed (run={self.run_id} "
                    f"seq={seq} type={event_type}): {err}"
                )
                return self._insert_failed(event_type, payload, seq)
            await self._fold_and_mirror(event_type, payload, seq)
            return seq
        return None  # pragma: no cover — the loop returns on every path

    async def _max_persisted_seq(self) -> Optional[int]:
        """The highest seq the transcript actually holds, or ``None`` when the
        read itself failed (which is NOT the same as "the run has no events" —
        that answers 0)."""
        try:
            from sqlalchemy import func, select

            from app.db.session import read_scope
            from app.models.agents import AgentRunTranscriptEvents

            async with read_scope() as session:
                found = (
                    await session.execute(
                        select(func.max(AgentRunTranscriptEvents.seq)).where(
                            AgentRunTranscriptEvents.run_id == self.run_id
                        )
                    )
                ).scalar()
            return int(found or 0)
        except Exception as err:  # noqa: BLE001 — telemetry never fails a run
            logger.warning(
                f"[RunEventWriter] could not re-seed seq (run={self.run_id}): {err}"
            )
            return None

    def _insert_failed(
        self, event_type: str, payload: dict[str, Any], seq: int
    ) -> None:
        """No row → no MIRROR: ``metadata_json.view`` must never claim an event
        the transcript does not hold. The in-memory view still folds, because
        in-process readers (the park reading ``view.question``, the budget hook
        reading cost) must see what happened in this process even with the DB
        down. Callers get ``None`` instead of a seq that names no row.

        ``_unpersisted_subagent`` is the sub-agent replay list. Since the seq
        retry above, it is a SECOND net rather than the only one — it still
        matters for the genuinely-unwritable case (the DB is down), where
        billing must not lose a child's cost.
        """
        nxt = apply_projection(self.views, event_type, payload, seq=seq)
        if nxt is not self.views:
            self.views = nxt
        if event_type in SUBAGENT_EVENT_TYPES:
            self._unpersisted_subagent.append((event_type, dict(payload)))
        return None

    def fold_local(self, kind: str, payload: dict[str, Any]) -> None:
        """Fold a local measurement (no event row). Mirrored with the next
        append so a gauge tick never costs its own UPDATE."""
        nxt = apply_projection(self.views, kind, payload)
        if nxt is not self.views:
            self.views = nxt
            self._pending_mirror = True

    async def _fold_and_mirror(
        self, event_type: str, payload: dict[str, Any], seq: int
    ) -> None:
        nxt = apply_projection(self.views, event_type, payload, seq=seq)
        if nxt is self.views and not self._pending_mirror:
            return
        self.views = nxt
        self._pending_mirror = False
        await self._mirror()
        if event_type == "subagent_done":
            await self._sync_cost_column()

    async def refold_external_slices(self) -> None:
        """Rebuild the slices a SECOND writer can touch — ``view.children`` /
        ``cost.by_child`` and ``view.outputs`` — from the persisted transcript,
        then recompute ``cost.spent_cents``.

        TWO writers touch those two slices on one run: the parent's own
        recorder (these in-memory views, mirrored as WHOLE values) and the
        background worker, which appends ``subagent_done`` through a separate
        ``for_run`` writer. Without this the parent's next mirror overwrote
        the worker's contribution wholesale — a child that finished while its
        parent was still running left ``async_pending`` stuck at +1 and its
        cost out of the parent's total.

        ``view.outputs`` is the same defect one slice over (三期 3a T8c 缺陷 1,
        真栈 run 348401200407189): the deliverables registry writes its
        ``deliverable`` event through ``RunEventWriter.for_run`` unless the
        live recorder is handed to it, and the live mirror then erased the
        outputs slice — ``metadata_json->'view'`` held 17 keys and ``outputs``
        was never one of them, so the cockpit's outputs cell, whose ONLY
        source is that slice, could not render on any run.

        ONE query feeds both: the fold registry already owns the per-event
        logic (``folds/deliverables.py``), so the scratch projection replays
        every externally-written type and each slice is lifted off it.

        Race-free by construction: both writers only ever APPEND, and
        ``append`` inserts the row BEFORE folding, so every event in these
        in-memory views is already in the transcript. The one exception is an
        event whose insert failed — folded in memory, no row — which is why
        ``_unpersisted_subagent`` is replayed on top.

        **What the guard costs, precisely.** The query runs on a mirror when
        ANY of these is true, and a mirror happens on every append that changes
        the view:

        * this writer has folded a sub-agent event (``children.total`` > 0);
        * this writer has folded a deliverable (``outputs.total`` > 0);
        * an append lost the ``(run_id, seq)`` race, i.e. a second writer is
          demonstrably on this run (``foreign_writer_seen``);
        * a sub-agent event failed to persist and is queued for replay.

        So a run with no sub-agents, no outputs and no competing writer pays
        ZERO extra reads — still the overwhelming majority. A run that has any
        of them pays ONE indexed read (``run_id`` + ``event_type IN``) per
        mirror from that point on, i.e. roughly one per remaining event. That
        is a real cost and it is the price of the slice being correct; it is
        bounded by the run's own event count, not by the transcript's size.

        The collision signal is what closes the last hole. Before it, the
        pre-check read only MEMORY, so a run whose outputs were ALL written by
        another writer (a turn that only calls ``GenerateShotImage``: the DBOS
        lane registers through ``for_run`` while the parent still runs) never
        opened the guard and had its ``view.outputs`` wiped by the next
        whole-value mirror. That second writer necessarily takes a seq this one
        wanted, so the conflict IS the notification.

        The WHOLE body is under one guard, including the pre-check that reads
        the stored ``children.total`` and the final slice assignments. Both
        call sites — ``_mirror`` (so, ``append``) and ``_finish`` — are on the
        live path of a turn, where this file's standing contract is that
        telemetry never fails a run. A stored count that is not a number, or a
        fold that produces an unusable shape, must cost a warning and a stale
        slice, not the turn: wiping or crashing over a decoration would be
        strictly worse than the drift this method exists to correct.
        """
        try:
            children = self.views["view"].get("children") or {}
            outputs = self.views["view"].get("outputs") or {}
            if (
                not int(children.get("total") or 0)
                and not int(outputs.get("total") or 0)
                and not self.foreign_writer_seen
                and not self._unpersisted_subagent
            ):
                return

            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models.agents import AgentRunTranscriptEvents

            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            select(
                                AgentRunTranscriptEvents.event_type,
                                AgentRunTranscriptEvents.payload,
                            )
                            .where(AgentRunTranscriptEvents.run_id == self.run_id)
                            .where(
                                AgentRunTranscriptEvents.event_type.in_(
                                    EXTERNALLY_WRITTEN_EVENT_TYPES
                                )
                            )
                            .order_by(AgentRunTranscriptEvents.seq.asc())
                        )
                    )
                    .mappings()
                    .all()
                )

            scratch = empty_views()
            for row in rows:
                scratch = apply_projection(
                    scratch, str(row["event_type"]), dict(row["payload"] or {})
                )
            for event_type, payload in self._unpersisted_subagent:
                scratch = apply_projection(scratch, event_type, payload)

            self.views["view"]["children"] = scratch["view"]["children"]
            self.views["cost"]["by_child"] = scratch["cost"]["by_child"]
            refolded_outputs = scratch["view"].get("outputs")
            if refolded_outputs:
                # ONLY when the log produced one. A ``deliverable`` whose
                # insert failed folded in memory without a row, and the
                # transcript cannot know about it — assigning an empty slice
                # over it would delete a count nothing can rebuild, while
                # leaving it is the same "degrade to what we have" the
                # failed-read branch below takes.
                self.views["view"]["outputs"] = refolded_outputs
            recompute_spent(self.views["cost"])
        except Exception as err:  # noqa: BLE001 — telemetry never fails a run
            logger.warning(
                f"[RunEventWriter] external re-fold skipped (run={self.run_id}): {err}"
            )

    async def _sync_cost_column(self) -> None:
        """Push the recomputed total into ``agent_runs.cost_cents`` for a run
        that has ALREADY ENDED (review I3).

        The background ``subagent_done`` is written onto the parent run by the
        worker, often turns after that run finished — and for an ended run the
        rollup reads the column, not the view. Restricted to ended rows
        because a live run's ``_finish`` computes the same total at the end
        anyway. Idempotent: the value is a derived total over a keyed
        ``by_child``, so a replayed event assigns the same number.
        """
        try:
            from sqlalchemy import update

            from app.db.session import write_scope
            from app.models.agents import AgentRuns

            async with write_scope() as session:
                await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self.run_id)
                    .where(AgentRuns.status != "running")
                    .values(cost_cents=float(self.views["cost"]["spent_cents"]))
                )
        except Exception as err:  # noqa: BLE001 — telemetry never fails a run
            logger.warning(
                f"[RunEventWriter] cost column sync failed (run={self.run_id}): {err}"
            )

    def mirror_keys(self) -> dict[str, Any]:
        """Whole values written into metadata_json — one place to read them."""
        view, cost = self.views["view"], self.views["cost"]
        return {
            "view": view,
            "cost": cost,
            # transition-only legacy keys (Task 7 removes)
            "todos": _legacy_todos(view),
            "turn_end_reason": (view.get("ended") or {}).get("reason"),
            "last_retry": view.get("retry"),
        }

    def mirror_stmt(self):
        """The UPDATE that writes every mirror key — a pure builder so its bind
        shapes can be asserted (pg dialect) and round-tripped against a real
        database in a rolled-back transaction."""
        from sqlalchemy import ARRAY, Text, bindparam, cast, func, update
        from sqlalchemy.dialects.postgresql import JSONB, array

        from app.models.agents import AgentRuns

        expr = func.coalesce(AgentRuns.metadata_json, cast("{}", JSONB))
        for key, value in self.mirror_keys().items():
            # Bind the Python value ONCE through the JSONB type. Passing
            # ``json.dumps(value)`` into ``cast(…, JSONB)`` double-encodes:
            # the JSONB bind processor serialises the *string* again and
            # PG stores a jsonb STRING — every phase-2 mirror row landed
            # that way (jsonb_typeof = 'string', 2026-09-05 真栈验收).
            expr = func.jsonb_set(
                expr,
                cast(array([key]), ARRAY(Text)),
                bindparam(None, _jsonable(value), type_=JSONB),
                True,
            )
        return (
            update(AgentRuns)
            .where(AgentRuns.id == self.run_id)
            .values(metadata_json=expr)
        )

    async def _mirror(self) -> None:
        # The mirror writes WHOLE ``view`` / ``cost`` values, so the two
        # externally-written slices are re-read from the event log first.
        await self.refold_external_slices()
        try:
            from app.db.session import write_scope

            async with write_scope() as session:
                await session.execute(self.mirror_stmt())
        except Exception as err:  # noqa: BLE001
            logger.warning(
                f"[RunEventWriter] view mirror failed (run={self.run_id}): {err}"
            )


def _jsonable(value: Any) -> Any:
    """Round-trip through json so non-JSON scalars (Decimal, datetime) become
    JSON-safe before the JSONB bind processor serialises the value once."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _legacy_todos(view: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Transition shape of ``metadata_json.todos`` for readers not yet on
    ``view`` (AgentResultBody's Steps table reads ``todos.todos``). Until
    2026-09-06 this returned ``todos: []`` — n/m survived, the table went
    blank (真栈 run 346496695717971: 3/3 with zero rows)."""
    step = view.get("step")
    if not step:
        return None
    return {
        "todos": list(view.get("todos") or []),
        "counts": {
            "total": step["total"],
            "completed": step["done"],
            "in_progress": 1 if step.get("label") else 0,
        },
    }
