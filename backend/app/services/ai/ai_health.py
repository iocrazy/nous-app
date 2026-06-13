"""AI capability health — make the capability→agent→model→provider→key
chain legible to users.

The mapping that decides which model+key each AI feature uses is spread
across task_assignment, ai_agents.model, and ai_providers — invisible in
the UI, and a broken key fails silently (the ark-key visual-analysis
outage that motivated this). This module resolves each capability through
the EXACT runtime resolver (resolve_task_provider_config) and reports an
actionable status so the Settings panel can show a status board.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai.providers.ai_provider_helpers import (
    get_ai_settings,
    resolve_task_provider_config,
)

logger = logging.getLogger(__name__)

# (task_assignment key, default agent slug, human label, needs a vision model?)
_CAPABILITIES: list[tuple[str, str, str, bool]] = [
    ("summarization", "summarize", "Rewrite (summary)", False),
    ("visual_analysis", "analyze", "Analyze (visual)", True),
    ("caption", "caption", "Image → Prompt", True),
    ("classify", "classify", "Auto Tag", True),
    ("translation", "translate", "Translation", False),
]

# Substrings that mark a model as vision-capable. Conservative: the
# capability is only flagged ``not_vision`` when NONE of these match, so a
# new VL model naming scheme degrades to "ok" (no false alarm) rather than
# a false "broken".
_VISION_HINTS = (
    "vl",
    "vision",
    "-v3",
    "gpt-4o",
    "gpt-4-turbo",
    "claude",
    "doubao-seed",
)


def _has_key(providers: dict, provider_key: str) -> bool:
    raw = (providers.get(provider_key) or {}).get("api_key")
    if isinstance(raw, list):
        return any(isinstance(k, str) and k.strip() for k in raw)
    return isinstance(raw, str) and bool(raw.strip())


def _looks_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _VISION_HINTS)


def _evaluate(
    *, provider_key: str, model: str, needs_vision: bool, providers: dict
) -> tuple[str, str]:
    if not model:
        return (
            "no_model",
            "The assigned agent has no model set — pick one in AI Library.",
        )
    if not provider_key:
        return (
            "unknown_provider",
            f"Model '{model}' maps to no known provider — check the agent's model.",
        )
    if not _has_key(providers, provider_key):
        return (
            "no_key",
            f"No API key for '{provider_key}'. Add it in the {provider_key} provider "
            "card above, or point this capability's agent at a provider you've configured.",
        )
    if needs_vision and not _looks_vision(model):
        return (
            "not_vision",
            f"'{model}' is a text-only model but this feature needs to see images. "
            "Switch the agent to a vision model (e.g. a *-VL-* model).",
        )
    return "ok", ""


async def get_capability_health(user_id: str) -> list[dict[str, Any]]:
    """One status row per AI capability. Never raises — a capability whose
    resolution crashes becomes an ``error`` row, its siblings unaffected."""
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    providers = ai_settings.get("ai_providers") or {}
    assignments = ai_settings.get("task_assignment") or {}

    rows: list[dict[str, Any]] = []
    for task_key, default_slug, label, needs_vision in _CAPABILITIES:
        try:
            provider_key, _config, model, slug = await resolve_task_provider_config(
                user_id, task_key, default_slug
            )
            status, hint = _evaluate(
                provider_key=provider_key,
                model=model,
                needs_vision=needs_vision,
                providers=providers,
            )
            rows.append(
                {
                    "capability": task_key,
                    "label": label,
                    "agent_slug": slug,
                    "assigned": bool((assignments.get(task_key) or "").strip()),
                    "model": model,
                    "provider": provider_key,
                    "needs_vision": needs_vision,
                    "status": status,
                    "hint": hint,
                }
            )
        except Exception:  # noqa: BLE001 — one bad capability must not sink the board
            logger.exception("[ai_health] capability %s resolution failed", task_key)
            rows.append(
                {
                    "capability": task_key,
                    "label": label,
                    "agent_slug": "",
                    "assigned": bool((assignments.get(task_key) or "").strip()),
                    "model": "",
                    "provider": "",
                    "needs_vision": needs_vision,
                    "status": "error",
                    "hint": "Could not resolve this capability — see server logs.",
                }
            )
    return rows


__all__ = ["get_capability_health"]
