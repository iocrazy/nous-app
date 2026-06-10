"""Shared AI-provider config helpers — extracted from the legacy
`app.tasks.ai_tasks` + `analysis_tasks` (PR-D7 phase 3 cleanup).

Pure helpers (no Celery). Used by:
    - `app.workflows.analyze_l1` (resolve_analyze_provider step)

Each helper preserves the exact behaviour of its `_xxx` predecessor
in the legacy tasks module.

§2.4b async DB-hoist: the DB-reading helpers are now ``async def`` and
``await`` the repos directly. They were previously sync, bridging to the
async repos via a fresh-event-loop ``_run_async`` shim — a pattern that is
incompatible with the SQLAlchemy ORM (asyncpg connections are loop-bound)
and that caused a prod connection-leak (2026-05-22). The sole caller,
``analyze_l1.resolve_analyze_provider``, is an ``async @DBOS.step`` awaited
from the async ``analyze_l1_workflow``, so awaiting here runs on the
workflow's own loop — no bridge, ORM-safe.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from loguru import logger


async def get_ai_settings(user_id: str) -> dict:
    """Load user's AI settings from the database."""
    from app.repositories.user_settings_repository import UserSettingsRepository

    repo = UserSettingsRepository()
    settings = await repo.get_by_user_id(user_id)
    if settings and settings.get("settings_json"):
        return settings["settings_json"].get("ai_settings", {})
    return {}


def get_provider_config(ai_settings: dict, provider_key: str) -> dict:
    """Extract provider config from AI settings."""
    providers = ai_settings.get("ai_providers", {})
    return providers.get(provider_key, {})


DEFAULT_ANALYZE_AGENT_SLUG = "analyze"


async def resolve_analyze_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve the visual-analysis agent's model + user's BYO provider config.

    Returns ``(provider_key, provider_config, model)``. Honors the user's
    ``task_assignment.visual_analysis`` setting — as of migration 142 (Phase 2
    PR 2.8b) that key stores an AI Library agent slug (e.g. 'analyze', or a
    custom 'test-analyze'). We resolve that slug to its ``ai_agents`` row, take
    its ``model``, derive the provider prefix, then pull the user's BYO entry
    for that provider out of ``ai_settings.ai_providers``.

    Falls back to the built-in ``analyze`` agent when the assignment is unset
    or the assigned slug doesn't resolve. When ``user_id`` is None or the agent
    row is missing, returns empty config and lets the service route through the
    factory's default.

    Bug history: this previously hardcoded ``get_by_slug("analyze")``, so a user
    who assigned a different agent (and only configured that agent's provider as
    BYO) always got the default ``analyze`` agent's model (qwen-max) with no
    matching provider config → empty config → "All connection attempts failed".
    """
    from app.repositories.agent_repository import get_agent_repository
    from app.services.ai.adapters.factory import provider_key_for_model

    # Load settings first so we can read the user's assigned agent slug. Reused
    # below for the BYO provider lookup, so this is a single read, not two.
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    assigned_slug = (
        (ai_settings.get("task_assignment") or {}).get("visual_analysis")
        or DEFAULT_ANALYZE_AGENT_SLUG
    ).strip() or DEFAULT_ANALYZE_AGENT_SLUG

    agent_repo = get_agent_repository()
    agent = await agent_repo.get_by_slug(assigned_slug)
    if not agent and assigned_slug != DEFAULT_ANALYZE_AGENT_SLUG:
        logger.warning(
            f"[AI] visual_analysis assigned agent '{assigned_slug}' not found; "
            f"falling back to '{DEFAULT_ANALYZE_AGENT_SLUG}'"
        )
        agent = await agent_repo.get_by_slug(DEFAULT_ANALYZE_AGENT_SLUG)

    model = ((agent or {}).get("model") or "").strip()
    if not model:
        logger.warning(
            f"[AI] visual_analysis agent '{assigned_slug}' missing or has no "
            "model; VisualAnalysisService will use built-in default"
        )
        return "", {}, ""

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        logger.warning(
            f"[AI] visual_analysis agent model '{model}' has unknown provider "
            "prefix; falling back to generic OpenAI-compatible adapter"
        )
        provider_key = ""

    if not user_id or not provider_key:
        return provider_key, {"model": model}, model

    provider_config = dict(get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return provider_key, provider_config, model
