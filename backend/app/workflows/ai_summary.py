"""ai_summary DBOS workflow — runs the `summarize` AI Library agent.

D9 refactor: was a bare OpenAI client call with hardcoded prompt. Now
delegates to SummarizeService, which composes the agent's
IDENTITY/SOUL/AGENT prompt + AgentRunner + RunRecorder so:
  - prompt comes from backend/seeds/agents/summarize/ (DB-editable)
  - agent_runs row lands per call (cost / tokens / outcome)
  - JSON output (summary + key_points + topics) persists to
    resource_summaries (canonical) + parsed_media.ai_rewrite_text
    (legacy compat used by the older summary card UI)
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Optional

from dbos import DBOS
from sqlalchemy import select

from app.db.scope import Scope, is_enforced, request_scope, system_request_scope


def _summary_inputs_select_stmt(parsed_media_id: int, user_id: str):
    """The transcript+resource lookup, column-level (not entity-level — the
    B4 row-shape lesson) so ``row["transcript"]``/``row.get("title")`` below
    read real column values. Already filters ``r.creator_id = :uid`` — this
    predicate is the query's OWN tenant filter (pre-dating the ORM choke
    point), kept as-is. Phase C final-review Minor 4: since ``user_id`` is a
    required, single, determinate arg AND is exactly the identity this
    predicate already filters on, the call site opens a real per-user
    ``request_scope(Scope(user_id=user_id))`` rather than
    ``system_request_scope`` — the choke point's injected filter then matches
    this query's own explicit filter exactly (defense in depth) instead of
    granting unrestricted SYSTEM visibility a query that already knows its
    one user doesn't need. Mirrors
    ``tags_repository._get_tag_counts_fallback``. Factored out so a real-
    aiosqlite row-shape test can import and exercise the exact production
    statement."""
    from app.models import ParsedMedia, Resources, ResourceTranscripts

    return (
        select(
            ResourceTranscripts.full_text.label("transcript"),
            ParsedMedia.id.label("pm_id"),
            ParsedMedia.title.label("title"),
            Resources.id.label("resource_id"),
        )
        .join(Resources, Resources.media_id == ParsedMedia.id)
        .join(ResourceTranscripts, ResourceTranscripts.resource_id == Resources.id)
        .where(ParsedMedia.id == parsed_media_id)
        .where(ResourceTranscripts.full_text.is_not(None))
        .where(Resources.creator_id == user_id)
        .limit(1)
    )


def _summary_inputs_exists_any_owner_stmt(parsed_media_id: int):
    """Same join chain as ``_summary_inputs_select_stmt`` but WITHOUT the
    ``creator_id`` filter — used only in ``load_summary_inputs``'s error path
    to tell "transcript missing" apart from "resource owned by someone
    else". No column-level/entity-level row-shape concern here (the caller
    only checks truthiness of ``.first()``), so ``select(Resources.id)`` is
    fine as-is."""
    from app.models import ParsedMedia, Resources, ResourceTranscripts

    return (
        select(Resources.id)
        .join(ParsedMedia, Resources.media_id == ParsedMedia.id)
        .join(ResourceTranscripts, ResourceTranscripts.resource_id == Resources.id)
        .where(ParsedMedia.id == parsed_media_id)
        .where(ResourceTranscripts.full_text.is_not(None))
        .limit(1)
    )


@DBOS.step()
async def load_summary_inputs(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Load transcript + user's preferred provider config + media title."""
    from app.db.session import read_scope

    # Resources carries UserScoped(creator_id); SCOPE_ENFORCE_RESOURCES
    # defaults false in code but production sets it true via
    # secrets/backend.env (CLAUDE.md 部署陷阱). This step has no ambient
    # per-request scope of its own (it's a DBOS step), so a scope wrap is
    # LOAD-BEARING once the flag is on — without it the JOIN through
    # Resources fail-closed raises UnscopedQueryError. A real per-user
    # request_scope (see _summary_inputs_select_stmt's docstring) rather
    # than system_request_scope — harmless no-op while the flag is off
    # (request_scope only sets the ambient ContextVar, no DB session, so no
    # is_enforced gate is needed here).
    async with request_scope(Scope(user_id=user_id)):
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        _summary_inputs_select_stmt(parsed_media_id, user_id)
                    )
                )
                .mappings()
                .first()
            )
    if not row:
        # Two failure modes shared one message and misled the 2026-08-07
        # diagnosis: "no transcript" also fired when the transcript existed
        # but belonged to a DIFFERENT resource owner (this query's own
        # Resources.creator_id == user_id filter hid the row). Probe once
        # without that filter to tell the two apart. SYSTEM scope: this
        # cross-tenant existence check IS the whole point of the probe — it
        # leaks nothing beyond a boolean (row exists / doesn't).
        probe_cm = (
            system_request_scope(
                reason="ai-summary error diagnostics: distinguish missing "
                "transcript from foreign-owned resource"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with probe_cm:
            async with read_scope() as session:
                foreign = (
                    await session.execute(
                        _summary_inputs_exists_any_owner_stmt(parsed_media_id)
                    )
                ).first()
        if foreign:
            raise RuntimeError(
                f"resource for parsed_media={parsed_media_id} exists but is "
                f"not owned by user={user_id} — dispatch identity mismatch, "
                f"not a missing transcript"
            )
        raise RuntimeError(
            f"no transcript for parsed_media={parsed_media_id} user={user_id}"
        )

    # Resolve the provider config through the shared typed resolver (A3 lift).
    # It owns the governance gate (platform-catalog first), the "no
    # user_settings" raise, the hardcoded provider-priority scan, and the
    # default_summary_model fallback. Passing settings_json=None lets the
    # resolver issue the same user_settings SELECT the workflow used to run —
    # and skip it entirely when the module is governance-locked.
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_summarization_config,
    )

    cfg = await resolve_summarization_config(user_id)

    return {
        "transcript": row["transcript"],
        "title": row.get("title") or "",
        "resource_id": str(row["resource_id"]),
        "provider_key": cfg.provider_key,
        "provider_config": cfg.provider_config,
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_summarize_agent(
    *,
    transcript: str,
    title: str,
    user_id: str,
    parsed_media_id: int,
    provider_key: str,
    provider_config: dict[str, Any],
    wf_id: Optional[str] = None,
) -> dict[str, Any]:
    """Invoke the `summarize` agent via SummarizeService → AgentRunner.
    Returns {summary, key_points, topics}. Each retry is a fresh agent
    call (token cost + agent_runs row each time).

    PR #237 audit: was sync ``def`` with ``asyncio.run()``. Now async
    so the AgentRunner / asyncpg pool stays on the executor's loop."""
    from app.services.ai.summarize.summarize_service import SummarizeService

    svc = SummarizeService(provider_key=provider_key, provider_config=provider_config)
    result = await svc.summarize(
        transcript=transcript,
        user_id=user_id,
        parsed_media_id=parsed_media_id,
        title=title,
        # task ↔ run bidirectional linkage (mig 282) — see analyze_l1.
        task_id=wf_id,
    )
    if result is None:
        raise RuntimeError("summarize agent returned None")

    return {
        "summary": result.summary,
        "key_points": result.key_points,
        "topics": result.topics,
        # Telemetry for resource_summaries.llm_model/llm_provider — both
        # columns were NULL since the table was created (2026-08-07 diag).
        "llm_model": (provider_config or {}).get("model", "") or "",
        "llm_provider": provider_key or "",
    }


@DBOS.step()
async def persist_summary(
    parsed_media_id: int,
    *,
    resource_id: str,
    summary: str,
    key_points: list[str],
    topics: list[str],
    llm_model: str = "",
    llm_provider: str = "",
) -> dict[str, Any]:
    """Persist summary to resource_summaries + parsed_media.ai_rewrite_text +
    resources.summary_status, atomically (one transaction).

    Uses ``write_scope()`` (not a bare ``engine.begin()``) so the three
    writes still commit together in ONE transaction — ``write_scope()``
    opens its own ``session.begin()`` (or joins an ambient unit_of_work) and
    commits once at block exit, preserving the prior raw-engine.begin()
    atomicity guarantee exactly. Do NOT split this into three separate
    ``write_scope()`` calls or you lose atomicity (same rationale the
    removed raw-SQL comment gave for the original ``engine.begin()``).
    """
    from sqlalchemy import func, update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.session import write_scope
    from app.models import ParsedMedia, Resources, ResourceSummaries

    rid = int(resource_id)  # resources.id is bigint; the ORM needs int, not str

    async with write_scope() as session:
        # resource_summaries / parsed_media carry no scope mixin — no wrap.
        insert_stmt = pg_insert(ResourceSummaries).values(
            resource_id=rid,
            summary_type="agent",
            summary_text=summary,
            # Native list/dict values — the JSONB bind processor serializes
            # them; no json.dumps()+CAST(... AS jsonb) round-trip needed.
            key_points=key_points or [],
            topics=topics or [],
            # Columns are varchar(50) — an over-length model/provider string
            # (some provider IDs run well past 50 chars) raises
            # StringDataRightTruncation at the DB, which combined with the
            # route-C rule 4 re-raise discards an already-paid-for summary.
            llm_model=(llm_model or "")[:50] or None,
            llm_provider=(llm_provider or "")[:50] or None,
        )
        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ResourceSummaries.resource_id],
            set_={
                "summary_text": insert_stmt.excluded.summary_text,
                "key_points": insert_stmt.excluded.key_points,
                "topics": insert_stmt.excluded.topics,
                "summary_type": insert_stmt.excluded.summary_type,
                "llm_model": insert_stmt.excluded.llm_model,
                "llm_provider": insert_stmt.excluded.llm_provider,
            },
        )
        await session.execute(insert_stmt)

        await session.execute(
            update(ParsedMedia)
            .where(ParsedMedia.id == parsed_media_id)
            .values(ai_rewrite_text=summary, ai_generated_at=func.now())
        )

        # Resources carries UserScoped(creator_id); bulk Core UPDATE on a
        # scoped model is FORBIDDEN under a real user Scope (app/db/scope.py's
        # write-path guard). This is a system-side status flip (the summarize
        # agent just finished, no per-request caller identity here), so
        # SYSTEM is correct — mirrors ai_transcription's mark_transcript_*
        # rationale. Wrap scoped to just this one statement (not the whole
        # transaction), per the minimal-wrap convention.
        scope_cm = (
            system_request_scope(
                reason="ai-summary workflow: mark summary status completed"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            await session.execute(
                update(Resources)
                .where(Resources.id == rid)
                .values(summary_status="completed")
            )

    return {
        "parsed_media_id": parsed_media_id,
        "resource_id": resource_id,
        "summary_len": len(summary),
        "key_points_count": len(key_points or []),
        "topics_count": len(topics or []),
    }


@DBOS.workflow()
async def ai_summary_workflow(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Production-shaped DBOS port of the ai_summary Celery task.

    - input: parsed_media_id (int) + user_id (uuid str)
    - output: {parsed_media_id, summary_len}
    - side-effects:
        parsed_media.ai_rewrite_text = <generated>
        parsed_media.ai_generated_at = now()
        resources.summary_status = 'completed'

    Dedup contract: same workflow_id (e.g. f"ai_summary-{user_id}-{parsed_media_id}-{ts}")
    will return cached result on retry. Use a unique workflow_id per user-initiated
    request — production handlers should generate `uuid4()` per click.
    """
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        inputs = await load_summary_inputs(parsed_media_id, user_id)
        await manager.update_progress(wf_id, 25, subtitle="Preparing transcript...")
        agent_out = await run_summarize_agent(
            transcript=inputs["transcript"],
            title=inputs.get("title", ""),
            user_id=user_id,
            parsed_media_id=parsed_media_id,
            provider_key=inputs.get("provider_key", ""),
            provider_config=inputs.get("provider_config", {}),
            wf_id=wf_id,
        )
        await manager.update_progress(wf_id, 70, subtitle="Summary generated")
        result = await persist_summary(
            parsed_media_id,
            resource_id=inputs["resource_id"],
            summary=agent_out["summary"],
            key_points=agent_out["key_points"],
            topics=agent_out["topics"],
            # .get() fallback: a DBOS step result replayed from an older
            # cached run may predate these keys — must not KeyError.
            llm_model=agent_out.get("llm_model", ""),
            llm_provider=agent_out.get("llm_provider", ""),
        )
        await manager.update_progress(wf_id, 100, subtitle="Summary saved")
        return result
    except Exception as e:  # noqa: BLE001
        # Route-C rule 4: record for task_tracking/UI, then RE-RAISE so
        # DBOS records ERROR — returning the dict made DBOS mark this
        # workflow SUCCESS while task_tracking said failed (observed live
        # 2026-08-08, wf 5a872175/1e63f80b).
        await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_summary",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
        raise
