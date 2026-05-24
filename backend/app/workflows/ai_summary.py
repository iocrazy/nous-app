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

import json
from typing import Any, Optional

from dbos import DBOS


@DBOS.step()
async def load_summary_inputs(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Load transcript + user's preferred provider config + media title."""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT rt.full_text AS transcript, pm.id AS pm_id, pm.title AS title, "
        "r.id AS resource_id "
        "FROM public.parsed_media pm "
        "JOIN public.resources r ON r.media_id = pm.id "
        "JOIN public.resource_transcripts rt ON rt.resource_id = r.id "
        "WHERE pm.id = :pid AND rt.full_text IS NOT NULL "
        "AND r.creator_id = :uid LIMIT 1",
        {"pid": parsed_media_id, "uid": user_id},
    )
    if not row:
        raise RuntimeError(
            f"no transcript for parsed_media={parsed_media_id} user={user_id}"
        )

    settings_row = await db_engine.fetch_one(
        "SELECT settings_json FROM public.user_settings WHERE user_id = :uid",
        {"uid": user_id},
    )
    if not settings_row:
        raise RuntimeError(f"no user_settings for {user_id}")
    settings = settings_row["settings_json"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    ai_settings = settings.get("ai_settings", {})
    providers = ai_settings.get("ai_providers", {}) or {}

    chosen_key: Optional[str] = None
    chosen_cfg: dict[str, Any] = {}
    for key in ("doubao", "qwen", "openai", "deepseek"):
        cfg = providers.get(key)
        if cfg and cfg.get("api_key") and cfg.get("enabled"):
            chosen_key, chosen_cfg = key, cfg
            break

    return {
        "transcript": row["transcript"],
        "title": row.get("title") or "",
        "resource_id": str(row["resource_id"]),
        "provider_key": chosen_key or "",
        "provider_config": {
            "api_key": chosen_cfg.get("api_key", ""),
            "base_url": chosen_cfg.get("base_url", ""),
            "app_id": chosen_cfg.get("app_id", ""),
            "model": chosen_cfg.get("selected_model")
            or ai_settings.get("default_summary_model")
            or "",
        },
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
    )
    if result is None:
        raise RuntimeError("summarize agent returned None")

    return {
        "summary": result.summary,
        "key_points": result.key_points,
        "topics": result.topics,
    }


@DBOS.step()
async def persist_summary(
    parsed_media_id: int,
    *,
    resource_id: str,
    summary: str,
    key_points: list[str],
    topics: list[str],
) -> dict[str, Any]:
    """Persist summary to resource_summaries + parsed_media.ai_rewrite_text +
    resources.summary_status, atomically (one transaction)."""
    from sqlalchemy import text

    from app.db import engine as db_engine

    payload_kp = json.dumps(key_points or [])
    payload_tp = json.dumps(topics or [])
    rid = int(resource_id)  # resources.id is bigint; asyncpg needs int, not str

    eng = db_engine.get_engine()
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.resource_summaries "
                "(resource_id, summary_type, summary_text, key_points, topics) "
                "VALUES (:rid, :stype, :stext, CAST(:kp AS jsonb), CAST(:tp AS jsonb)) "
                "ON CONFLICT (resource_id) DO UPDATE SET "
                "summary_text = EXCLUDED.summary_text, "
                "key_points = EXCLUDED.key_points, "
                "topics = EXCLUDED.topics, "
                "summary_type = EXCLUDED.summary_type"
            ),
            {
                "rid": rid,
                "stype": "agent",
                "stext": summary,
                "kp": payload_kp,
                "tp": payload_tp,
            },
        )
        await conn.execute(
            text(
                "UPDATE public.parsed_media SET ai_rewrite_text = :txt, "
                "ai_generated_at = now() WHERE id = :pid"
            ),
            {"txt": summary, "pid": parsed_media_id},
        )
        await conn.execute(
            text(
                "UPDATE public.resources SET summary_status = 'completed' "
                "WHERE id = :rid"
            ),
            {"rid": rid},
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
    from app.workflows._failure_handler import record_workflow_failure

    try:
        inputs = await load_summary_inputs(parsed_media_id, user_id)
        agent_out = await run_summarize_agent(
            transcript=inputs["transcript"],
            title=inputs.get("title", ""),
            user_id=user_id,
            parsed_media_id=parsed_media_id,
            provider_key=inputs.get("provider_key", ""),
            provider_config=inputs.get("provider_config", {}),
        )
        return await persist_summary(
            parsed_media_id,
            resource_id=inputs["resource_id"],
            summary=agent_out["summary"],
            key_points=agent_out["key_points"],
            topics=agent_out["topics"],
        )
    except Exception as e:  # noqa: BLE001
        return await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_summary",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
