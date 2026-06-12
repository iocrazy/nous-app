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
DEFAULT_TRANSLATE_AGENT_SLUG = "translate"


async def resolve_task_provider_config(
    user_id: Optional[str],
    task_key: str,
    default_slug: str,
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve a task's assigned agent slug + model + user's BYO provider config.

    Generic form of ``resolve_analyze_provider_config`` (which delegates
    here): reads ``task_assignment[task_key]`` for the agent slug (falling
    back to ``default_slug``), resolves the slug to its ``ai_agents`` row,
    takes its model, derives the provider key from the model prefix, and
    merges the user's BYO entry for that provider.

    Returns ``(provider_key, provider_config, model, agent_slug)`` — the
    resolved slug is returned so the caller composes the SAME agent whose
    model was resolved (see #622/#623: prompt agent and model agent must
    match or the composed model overrides the resolved one).
    """
    from app.repositories.agent_repository import get_agent_repository
    from app.services.ai.adapters.factory import provider_key_for_model

    # Load settings first so we can read the user's assigned agent slug.
    # Reused below for the BYO provider lookup — a single read, not two.
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    assigned_slug = (
        (ai_settings.get("task_assignment") or {}).get(task_key) or default_slug
    ).strip() or default_slug
    resolved_slug = assigned_slug

    agent_repo = get_agent_repository()
    agent = await agent_repo.get_by_slug(assigned_slug)
    if not agent and assigned_slug != default_slug:
        logger.warning(
            f"[AI] {task_key} assigned agent '{assigned_slug}' not found; "
            f"falling back to '{default_slug}'"
        )
        agent = await agent_repo.get_by_slug(default_slug)
        resolved_slug = default_slug

    model = ((agent or {}).get("model") or "").strip()
    if not model:
        logger.warning(
            f"[AI] {task_key} agent '{assigned_slug}' missing or has no "
            "model; caller will use built-in default"
        )
        return "", {}, "", resolved_slug

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        logger.warning(
            f"[AI] {task_key} agent model '{model}' has unknown provider "
            "prefix; falling back to generic OpenAI-compatible adapter"
        )
        provider_key = ""

    if not user_id or not provider_key:
        return provider_key, {"model": model}, model, resolved_slug

    provider_config = dict(get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return provider_key, provider_config, model, resolved_slug


async def resolve_analyze_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the visual-analysis agent slug + model + user's BYO provider config.

    Returns ``(provider_key, provider_config, model, agent_slug)``. Honors the
    user's ``task_assignment.visual_analysis`` setting — as of migration 142
    (Phase 2 PR 2.8b) that key stores an AI Library agent slug (e.g. 'analyze',
    or a custom 'test-analyze'). We resolve that slug to its ``ai_agents`` row,
    take its ``model``, derive the provider prefix, then pull the user's BYO
    entry for that provider out of ``ai_settings.ai_providers``.

    ``agent_slug`` (the resolved slug, after any fallback) is returned so the
    caller composes the SAME agent's prompt as the one whose model we resolved.
    VisualAnalysisService composes the agent's IDENTITY/SOUL/AGENT to build its
    system prompt, and ``composed.model`` (the composed agent's model) is what
    actually drives the adapter — so the prompt agent and the model agent MUST
    be the same one, else the composed model overrides the resolved one.

    Falls back to the built-in ``analyze`` agent when the assignment is unset
    or the assigned slug doesn't resolve. When ``user_id`` is None or the agent
    row is missing, returns empty config and lets the service route through the
    factory's default.

    Bug history:
      - #622 fixed the slug read here, but the run still used qwen-max because
        VisualAnalysisService hardcoded the prompt agent to 'analyze', and the
        composed (qwen-max) model overrode the resolved doubao one. Returning
        ``agent_slug`` lets the caller compose the right agent end-to-end.
      - Originally hardcoded ``get_by_slug("analyze")``, so a user who assigned a
        different agent (and only configured that agent's provider as BYO) always
        got 'analyze'\\'s qwen-max with no matching config → empty config →
        "All connection attempts failed".
    """
    return await resolve_task_provider_config(
        user_id, "visual_analysis", DEFAULT_ANALYZE_AGENT_SLUG
    )


async def resolve_translate_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the translation agent slug + model + user's BYO provider config.

    Honors ``task_assignment.translation`` (an AI Library agent slug),
    defaulting to the built-in ``translate`` agent. Same return shape as
    the other resolvers: ``(provider_key, provider_config, model, slug)``.
    """
    return await resolve_task_provider_config(
        user_id, "translation", DEFAULT_TRANSLATE_AGENT_SLUG
    )
