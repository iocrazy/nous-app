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

import asyncio
import json
import os
from typing import Any, Optional

import psycopg
from dbos import DBOS
from loguru import logger


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


@DBOS.step()
def load_summary_inputs(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Load transcript + user's preferred provider config (key/base_url
    snapshot for the agent's resolved model) + media title for prompt
    context. SummarizeService picks the right adapter from the
    provider_key + provider_config we hand it."""
    with psycopg.connect(_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            """
            SELECT rt.full_text AS transcript,
                   pm.id        AS pm_id,
                   pm.title     AS title,
                   r.id         AS resource_id
            FROM public.parsed_media pm
            JOIN public.resources r ON r.media_id = pm.id
            JOIN public.resource_transcripts rt ON rt.resource_id = r.id
            WHERE pm.id = %s
              AND rt.full_text IS NOT NULL
              AND r.creator_id = %s::uuid
            LIMIT 1
            """,
            (parsed_media_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                f"no transcript for parsed_media={parsed_media_id} "
                f"user={user_id}"
            )

        cur = conn.execute(
            "SELECT settings_json FROM public.user_settings WHERE user_id = %s",
            (user_id,),
        )
        settings_row = cur.fetchone()

    if not settings_row:
        raise RuntimeError(f"no user_settings for {user_id}")
    settings = settings_row["settings_json"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    ai_settings = settings.get("ai_settings", {})
    providers = ai_settings.get("ai_providers", {}) or {}

    # Pick first enabled provider — SummarizeService will derive the
    # adapter from the agent's resolved model + this BYO config.
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
def run_summarize_agent(
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
    call (token cost + agent_runs row each time)."""
    from app.services.ai.summarize.summarize_service import SummarizeService

    svc = SummarizeService(
        provider_key=provider_key, provider_config=provider_config
    )
    result = asyncio.run(
        svc.summarize(
            transcript=transcript,
            user_id=user_id,
            parsed_media_id=parsed_media_id,
            title=title,
        )
    )
    if result is None:
        raise RuntimeError("summarize agent returned None")

    return {
        "summary": result.summary,
        "key_points": result.key_points,
        "topics": result.topics,
    }


@DBOS.step()
def persist_summary(
    parsed_media_id: int,
    *,
    resource_id: str,
    summary: str,
    key_points: list[str],
    topics: list[str],
) -> dict[str, Any]:
    """Persist the structured summary in 3 places:
      1. resource_summaries (canonical: summary_text + JSONB key_points
         + JSONB topics) — what the API returns
      2. parsed_media.ai_rewrite_text (legacy compat for older UI bits
         that still read this column directly)
      3. resources.summary_status='completed' (drives MediaCard's AI
         Intent badge color)
    """
    payload_kp = json.dumps(key_points or [])
    payload_tp = json.dumps(topics or [])

    with psycopg.connect(_dsn()) as conn:
        conn.execute("SET ROLE service_role")
        # 1. resource_summaries (upsert by resource_id)
        conn.execute(
            """
            INSERT INTO public.resource_summaries
              (resource_id, summary_type, summary_text, key_points, topics)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (resource_id) DO UPDATE SET
              summary_text = EXCLUDED.summary_text,
              key_points   = EXCLUDED.key_points,
              topics       = EXCLUDED.topics,
              summary_type = EXCLUDED.summary_type
            """,
            (resource_id, "agent", summary, payload_kp, payload_tp),
        )

        # 2. parsed_media.ai_rewrite_text (legacy)
        conn.execute(
            """UPDATE public.parsed_media
               SET ai_rewrite_text = %s,
                   ai_generated_at = now()
               WHERE id = %s""",
            (summary, parsed_media_id),
        )

        # 3. resources.summary_status (UI badge)
        conn.execute(
            """UPDATE public.resources
               SET summary_status = 'completed'
               WHERE id = %s""",
            (resource_id,),
        )
        conn.commit()

    return {
        "parsed_media_id": parsed_media_id,
        "resource_id": resource_id,
        "summary_len": len(summary),
        "key_points_count": len(key_points or []),
        "topics_count": len(topics or []),
    }


@DBOS.workflow()
def ai_summary_workflow(parsed_media_id: int, user_id: str) -> dict[str, Any]:
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
        inputs = load_summary_inputs(parsed_media_id, user_id)
        agent_out = run_summarize_agent(
            transcript=inputs["transcript"],
            title=inputs.get("title", ""),
            user_id=user_id,
            parsed_media_id=parsed_media_id,
            provider_key=inputs.get("provider_key", ""),
            provider_config=inputs.get("provider_config", {}),
        )
        return persist_summary(
            parsed_media_id,
            resource_id=inputs["resource_id"],
            summary=agent_out["summary"],
            key_points=agent_out["key_points"],
            topics=agent_out["topics"],
        )
    except Exception as e:  # noqa: BLE001
        return record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_summary",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
