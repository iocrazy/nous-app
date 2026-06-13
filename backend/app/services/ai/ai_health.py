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

from app.services.ai.ai_health_runtime import fetch_runtime_summary
from app.services.ai.providers.ai_provider_helpers import (
    get_ai_settings,
    resolve_task_provider_config,
)

logger = logging.getLogger(__name__)

# (task_assignment key, default agent slug, human label, needs a vision model?,
#  task_tracking task_type or None). The task_type links a capability to its
# workflow runs so the runtime layer can flag a *currently failing* feature
# even when its static config looks healthy. Capabilities that run inline
# (no DBOS workflow → no task_tracking row) carry None and get config-only
# health, as before. visual_analysis (ai_extract) is the capability that
# motivated this — the ark-key AccessDenied outage left a healthy-looking
# board while every call failed.
_CAPABILITIES: list[tuple[str, str, str, bool, str | None]] = [
    ("summarization", "summarize", "Rewrite (summary)", False, None),
    ("visual_analysis", "analyze", "Analyze (visual)", True, "ai_extract"),
    ("caption", "caption", "Image → Prompt", True, None),
    ("classify", "classify", "Auto Tag", True, None),
    ("translation", "translate", "Translation", False, None),
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


def _apply_runtime(
    status: str, hint: str, *, model: str, provider: str, rt: dict[str, Any]
) -> tuple[str, str]:
    """Overlay runtime reality on a config-healthy capability.

    When the static config is ``ok`` but the most recent tracked run failed,
    the capability is *currently* broken (the model+key resolve, yet the call
    is rejected — e.g. AccessDenied). Surface it as ``runtime_failing`` with
    the real error. Config problems keep priority: they are the actionable
    root cause, so a runtime blip never masks a missing key.
    """
    if status != "ok" or not rt.get("latest_failed"):
        return status, hint
    err = (rt.get("last_error") or "").strip() or "see Task Center for details"
    return (
        "runtime_failing",
        f"Latest run failed: {err}. '{model}' and the {provider} key resolve, "
        "but the call is being rejected — verify the key has access to this model.",
    )


async def get_capability_health(user_id: str) -> list[dict[str, Any]]:
    """One status row per AI capability. Never raises — a capability whose
    resolution crashes becomes an ``error`` row, its siblings unaffected."""
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    providers = ai_settings.get("ai_providers") or {}
    assignments = ai_settings.get("task_assignment") or {}

    task_types = [tt for *_rest, tt in _CAPABILITIES if tt]
    runtime = await fetch_runtime_summary(user_id, task_types) if user_id else {}

    rows: list[dict[str, Any]] = []
    for task_key, default_slug, label, needs_vision, task_type in _CAPABILITIES:
        assigned = bool((assignments.get(task_key) or "").strip())
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
            row = {
                "capability": task_key,
                "label": label,
                "agent_slug": slug,
                "assigned": assigned,
                "model": model,
                "provider": provider_key,
                "needs_vision": needs_vision,
                "status": status,
                "hint": hint,
            }
            if task_type:
                rt = runtime.get(task_type) or {}
                status, hint = _apply_runtime(
                    status, hint, model=model, provider=provider_key, rt=rt
                )
                row.update(
                    {
                        "status": status,
                        "hint": hint,
                        "task_type": task_type,
                        "recent_runs": rt.get("recent_runs", 0),
                        "recent_failures": rt.get("recent_failures", 0),
                        "last_error": rt.get("last_error", ""),
                    }
                )
            rows.append(row)
        except Exception:  # noqa: BLE001 — one bad capability must not sink the board
            logger.exception("[ai_health] capability %s resolution failed", task_key)
            rows.append(
                {
                    "capability": task_key,
                    "label": label,
                    "agent_slug": "",
                    "assigned": assigned,
                    "model": "",
                    "provider": "",
                    "needs_vision": needs_vision,
                    "status": "error",
                    "hint": "Could not resolve this capability — see server logs.",
                }
            )
    return rows


__all__ = ["get_capability_health"]
