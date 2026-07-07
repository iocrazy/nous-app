"""Governed LLM call for agent-memory promotion classification/scrub (Phase C1).

Mirrors agent_memory_consolidator.default_consolidator: routes through the
adapter factory using the global platform settings, with a cheap Qwen tier
as fallback. Returns "" on any error (best-effort, never raises).
"""

from __future__ import annotations

from loguru import logger

_FALLBACK_PROMOTION_MODEL = "qwen-turbo"
_SYSTEM_MESSAGE = (
    "You classify agent memory for team/project sharing and scrub PII. "
    "Output only a JSON object with keys: shareable, confidence, justification, "
    "scrubbed_body_md."
)


async def default_promotion_evaluator(prompt: str, model: str = "") -> str:
    """Governed LLM call for the promotion classification/scrub step.

    Routes through ``model`` via the adapter factory (the same convention
    as the consolidator's default_consolidator and session_memory_runner),
    falling back to a cheap Qwen tier when the model is empty or its prefix
    is unrecognised by the factory.

    Returns "" on any error — promotion evaluation is best-effort and must
    never break the caller.
    """
    try:
        from uuid import UUID

        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.providers.ai_provider_helpers import (
            resolve_db_adapter,
        )

        promotion_model = (model or "").strip() or _FALLBACK_PROMOTION_MODEL
        try:
            # DB-only credentials (铁律 2026-07-07): platform catalog → raise.
            adapter = await resolve_db_adapter(promotion_model, "agent_memory")
        except ValueError:
            # Unknown/unconfigured model → fall back to the cheap default
            # (itself DB-resolved; a miss lands in the outer best-effort
            # except and returns "").
            promotion_model = _FALLBACK_PROMOTION_MODEL
            adapter = await resolve_db_adapter(promotion_model, "agent_memory")

        composed = ComposedSystemPrompt(
            # Use the nil UUID — maintenance call needs no real agent identity.
            agent_id=UUID(int=0),
            agent_slug="agent_memory_promotion_evaluator",
            model=promotion_model,
            temperature=0.0,
            max_tokens=1024,
            system_message=_SYSTEM_MESSAGE,
            tools=[],
            skill_manifest=[],
            cache_fingerprint="agent_memory_promotion_v1",
        )
        result = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return result.get("content") or ""
    except Exception as exc:
        logger.debug(f"agent_memory promotion evaluator failed: {exc}")
        return ""


__all__ = ["default_promotion_evaluator"]
