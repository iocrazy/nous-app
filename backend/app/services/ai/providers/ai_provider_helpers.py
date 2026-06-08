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


async def resolve_analyze_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve analyze agent's model + user's BYO provider config.

    Returns ``(provider_key, provider_config, model)``. Reads the
    ``analyze`` ``ai_agents`` row to get its ``model``, derives the
    provider prefix, then pulls the user's BYO entry for that provider
    out of ``ai_settings.ai_providers``.

    When ``user_id`` is None or the agent row is missing, returns empty
    config and lets the service route through the factory's default.
    """
    from app.repositories.agent_repository import get_agent_repository
    from app.services.ai.adapters.factory import provider_key_for_model

    agent_repo = get_agent_repository()
    agent = await agent_repo.get_by_slug("analyze")
    model = ((agent or {}).get("model") or "").strip()
    if not model:
        logger.warning(
            "[AI] analyze agent row missing or has no model; "
            "VisualAnalysisService will use built-in default"
        )
        return "", {}, ""

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        logger.warning(
            f"[AI] analyze agent model '{model}' has unknown provider prefix; "
            "falling back to generic OpenAI-compatible adapter"
        )
        provider_key = ""

    if not user_id or not provider_key:
        return provider_key, {"model": model}, model

    ai_settings = await get_ai_settings(user_id)
    provider_config = dict(get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return provider_key, provider_config, model
