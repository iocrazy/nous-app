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

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from loguru import logger


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
    team_id: Optional[int] = None
    project_id: Optional[int] = None
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

    HEARTBEAT_RATE_LIMIT_S: float = 15.0  # local, DB-write throttle
    EVENT_VALUE_MAX_CHARS: int = 4000  # per-field payload truncation

    async def __aenter__(self) -> "RunRecorder":
        try:
            await self._pre_flight_check_paused()
            await self._snapshot_price()
            await self._insert_row()
        except AgentPausedError:
            # Re-raise — caller needs to surface the pause to the user,
            # and we intentionally do NOT persist a run row for pre-flight rejections.
            raise
        except Exception as err:
            # Telemetry failures don't break agent runs. Log and continue
            # without a persisted row; methods below become no-ops because
            # self.run_id stays None.
            logger.error(
                f"[RunRecorder] start failed (telemetry disabled for this run): {err}"
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
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

    async def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append one transcript event (mig 285, paperclip port P3).

        AgentRunner calls this as the run executes (user message → tool
        calls → assistant output); the Runs detail pane renders the stream
        as the Transcript section. Best-effort like every other telemetry
        write — a failed insert never breaks the run. Payload string values
        are truncated so a huge tool result can't bloat the table.
        """
        if self.run_id is None:
            return
        self._event_seq += 1
        try:
            from app.db import get_async_supabase_admin

            client = await get_async_supabase_admin()
            await (
                client.table("agent_run_events")
                .insert(
                    {
                        "run_id": str(self.run_id),
                        "seq": self._event_seq,
                        "event_type": event_type,
                        "payload": _truncate_payload(
                            payload, self.EVENT_VALUE_MAX_CHARS
                        ),
                    }
                )
                .execute()
            )
        except Exception as err:
            logger.warning(
                f"[RunRecorder] record_event failed "
                f"(run={self.run_id} seq={self._event_seq}): {err}"
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
            from app.db import get_async_supabase_admin

            client = await get_async_supabase_admin()
            await (
                client.table("agent_runs")
                .update({"heartbeat_at": "now()"})
                .eq("id", str(self.run_id))
                .eq("status", "running")
                .execute()
            )
        except Exception as err:
            logger.warning(f"[RunRecorder] heartbeat failed for {self.run_id}: {err}")

    async def check_cancelled(self) -> bool:
        """Poll cancel_requested. Runner should break out of its loop when true."""
        if self.run_id is None:
            return False
        try:
            from app.db import get_async_supabase_admin

            client = await get_async_supabase_admin()
            result = (
                await client.table("agent_runs")
                .select("cancel_requested")
                .eq("id", str(self.run_id))
                .maybe_single()
                .execute()
            )
            if result and result.data and result.data.get("cancel_requested"):
                self._cancelled = True
                return True
        except Exception as err:
            logger.warning(
                f"[RunRecorder] cancel check failed for {self.run_id}: {err}"
            )
        return False

    # -------- internal ---------------------------------------------------

    async def _pre_flight_check_paused(self) -> None:
        from app.db import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = (
            await client.table("ai_agents")
            .select("paused_reason,max_concurrent_runs")
            .eq("id", str(self.agent_id))
            .maybe_single()
            .execute()
        )
        if result and result.data and result.data.get("paused_reason"):
            reason = result.data["paused_reason"]
            raise AgentPausedError(f"agent paused (reason={reason})")

        # mig 286 (paperclip P4): per-agent concurrency cap. Counted across
        # ALL users — the limit protects the agent/provider, not one caller.
        # Race window between count and insert is accepted (paperclip's is
        # too): the cap is a throttle, not a mutex.
        limit = (result.data or {}).get("max_concurrent_runs") if result else None
        if limit:
            running_q = (
                await client.table("agent_runs")
                .select("id", count="exact", head=True)
                .eq("agent_id", str(self.agent_id))
                .eq("status", "running")
                .execute()
            )
            running = running_q.count or 0
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
            from app.db import get_async_supabase_admin

            client = await get_async_supabase_admin()
            query = (
                client.table("ai_model_prices")
                .select(
                    "prompt_cents_per_1k,completion_cents_per_1k,"
                    "cached_input_cents_per_1k,effective_at"
                )
                .eq("model", self.model)
            )
            if self.provider:
                query = query.eq("provider", self.provider)
            result = await query.order("effective_at", desc=True).limit(1).execute()
            if result.data:
                row = result.data[0]
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
        from app.db import get_async_supabase_admin

        client = await get_async_supabase_admin()
        payload = {
            "agent_id": str(self.agent_id),
            "user_id": str(self.user_id),
            "session_id": str(self.session_id) if self.session_id else None,
            "team_id": self.team_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "status": "running",
            "trigger": self.trigger,
            "model": self.model,
            "provider": self.provider,
            "input_summary": self.input_summary,
            "prompt_cents_per_1k_snapshot": self._prompt_rate,
            "completion_cents_per_1k_snapshot": self._completion_rate,
            "metadata_json": self.metadata or {},
        }
        # Drop None values so DB defaults (e.g., now()) apply.
        payload = {k: v for k, v in payload.items() if v is not None}
        if self.issue_id is not None:
            payload["issue_id"] = self.issue_id
        result = await client.table("agent_runs").insert(payload).execute()
        if result.data:
            # agent_runs.id became a BIGINT Snowflake in migration 232 (was
            # UUID at #57). Keep run_id as the string form of whatever the DB
            # returned — every consumer only str()s it (the .eq("id", …)
            # filters and reconcile_run). Wrapping in UUID() raised ValueError
            # on the bigint, which __aenter__ swallowed as "telemetry disabled"
            # → no agent_runs row was finalised for any run after mig 232.
            self.run_id = str(result.data[0]["id"])
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
            from app.db import get_async_supabase_admin

            client = await get_async_supabase_admin()
            current = (
                await client.table("task_tracking")
                .select("metadata")
                .eq("dbos_workflow_id", self.task_id)
                .maybe_single()
                .execute()
            )
            if not current or current.data is None:
                return
            merged = dict(current.data.get("metadata") or {})
            merged["run_id"] = self.run_id
            await (
                client.table("task_tracking")
                .update({"agent_id": str(self.agent_id), "metadata": merged})
                .eq("dbos_workflow_id", self.task_id)
                .execute()
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
        from app.db import get_async_supabase_admin

        cost_cents: Optional[float] = None
        if self._prompt_rate is not None and self._completion_rate is not None:
            cost_cents = self.compute_cost_cents()

        updates: dict[str, Any] = {
            "status": status,
            "ended_at": "now()",
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "cached_input_tokens": self._cached_input_tokens,
            "skill_slugs_used": self._skill_slugs_used,
        }
        if cost_cents is not None:
            updates["cost_cents"] = cost_cents
        if self._output_summary is not None:
            updates["output_summary"] = self._output_summary
        if error_code is not None:
            updates["error_code"] = error_code
        if error_message is not None:
            updates["error_message"] = error_message

        client = await get_async_supabase_admin()
        await (
            client.table("agent_runs")
            .update(updates)
            .eq("id", str(self.run_id))
            .eq("status", "running")  # idempotent guard
            .execute()
        )

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
    """JSON-safe copy of an event payload with long string values truncated.

    One level deep is enough — event payloads are flat ({content}, {tool,
    args, result}); nested dicts are stringified-then-truncated so a deep
    tool result can't sneak megabytes past the cap.
    """
    out: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if isinstance(v, str):
            out[k] = _truncate(v, max_chars)
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            try:
                import json as _json

                out[k] = _truncate(
                    _json.dumps(v, ensure_ascii=False, default=str), max_chars
                )
            except Exception:  # noqa: BLE001 — telemetry only
                out[k] = _truncate(repr(v), max_chars)
    return out
