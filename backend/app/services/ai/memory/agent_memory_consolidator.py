"""Governed LLM call for agent-memory consolidation (Phase B).

Mirrors session_memory_runner._default_summarizer: routes through the
adapter factory using the global platform settings, with a cheap Qwen
tier as fallback. Returns "" on any error (best-effort, never raises).
"""

from __future__ import annotations

from loguru import logger

_FALLBACK_CONSOLIDATION_MODEL = "qwen-turbo"
_SYSTEM_MESSAGE = (
    "You distil durable agent memory. " "Output only a JSON array of memory entries."
)


async def default_consolidator(prompt: str, model: str = "") -> str:
    """Governed LLM call for the /dream consolidation step.

    Routes through ``model`` via the adapter factory (the same convention
    as the chat compactor's summarizer and session_memory_runner), falling
    back to a cheap Qwen tier when the model is empty or its prefix is
    unrecognised by the factory.

    Returns "" on any error — consolidation is best-effort and must never
    break the caller.
    """
    try:
        from uuid import UUID

        from app.core.config import settings
        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.adapters.factory import get_adapter

        consolidation_model = (model or "").strip() or _FALLBACK_CONSOLIDATION_MODEL
        try:
            adapter = get_adapter(consolidation_model, settings)
        except ValueError:
            # Unknown model prefix → fall back to the cheap default provider.
            consolidation_model = _FALLBACK_CONSOLIDATION_MODEL
            adapter = get_adapter(consolidation_model, settings)

        composed = ComposedSystemPrompt(
            # Use the nil UUID — maintenance call needs no real agent identity.
            agent_id=UUID(int=0),
            agent_slug="agent_memory_consolidator",
            model=consolidation_model,
            temperature=0.1,
            max_tokens=2048,
            system_message=_SYSTEM_MESSAGE,
            tools=[],
            skill_manifest=[],
            cache_fingerprint="agent_memory_consolidation_v1",
        )
        result = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return result.get("content") or ""
    except Exception as exc:
        logger.debug(f"agent_memory consolidator failed: {exc}")
        return ""


__all__ = ["default_consolidator"]
