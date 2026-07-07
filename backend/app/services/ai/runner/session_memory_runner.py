"""Wave 5b (B4): chat-side glue for SessionMemoryService.

Wires the per-process repo + a cheap-LLM summarizer into one call site
the chat service can fire-and-forget. The hot path (chat response) does
not await this — it just dispatches an asyncio task and returns.

Failures here are SILENT by design: a stale session_memory is preferable
to breaking chat. Errors log + the next maybe_update gets another shot.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.agent_framework.session_memory import (
    SessionMemoryService,
    SessionMemoryTrigger,
)
from app.repositories.session_memory_repository import (
    SessionMemoryRepository,
    get_session_memory_repository,
)

# Fallback model when the agent's own model is unknown/empty — a cheap Qwen
# tier (DashScope) for session-note maintenance.
_FALLBACK_SUMMARY_MODEL = "qwen-turbo"


# Cheap-summarizer prompt routed through the agent's OWN provider, the same way
# the chat compactor's summarizer does (DB-resolved platform credentials).
# Kept here (not in agent_framework) to avoid pulling settings into a primitive.
async def _default_summarizer(prompt: str, model: str = "") -> str:
    """Cheap LLM call for session-memory maintenance.

    Audit #17: previously hardcoded a QwenAdapter, so this silently no-op'd
    for any user/agent on a non-Qwen provider (e.g. Doubao-only). Now routes
    through the agent's own ``model`` via the adapter factory (platform keys,
    same convention as the chat compactor's summarizer), falling back to a
    cheap Qwen tier when the model is empty or its prefix is unknown.
    """
    try:
        from uuid import UUID

        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.providers.ai_provider_helpers import (
            resolve_db_adapter,
        )

        summary_model = (model or "").strip() or _FALLBACK_SUMMARY_MODEL
        try:
            # DB-only credentials (铁律 2026-07-07): platform catalog → raise.
            adapter = await resolve_db_adapter(summary_model, "agent_memory")
        except ValueError:
            # Unknown/unconfigured model → fall back to the cheap default
            # (itself DB-resolved; a miss lands in the outer best-effort
            # except).
            summary_model = _FALLBACK_SUMMARY_MODEL
            adapter = await resolve_db_adapter(summary_model, "agent_memory")

        composed = ComposedSystemPrompt(
            # Audit #17: ComposedSystemPrompt.agent_id is a required UUID — the
            # old ``agent_id=None`` raised a ValidationError that the broad
            # except swallowed, so this summarizer never actually ran. Use the
            # nil UUID (maintenance call needs no real agent identity).
            agent_id=UUID(int=0),
            agent_slug="session_memory_updater",
            model=summary_model,
            temperature=0.1,
            max_tokens=2048,
            system_message="You maintain markdown session notes. Output only the markdown.",
            tools=[],
            skill_manifest=[],
            cache_fingerprint="session_memory_v1",
        )
        result = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return result.get("content") or ""
    except Exception as exc:
        logger.debug(f"session_memory summarizer failed: {exc}")
        return ""


async def maybe_update_session_memory(
    *,
    session_id: str,
    messages: list[dict],
    model: str = "",
    repo: Optional[SessionMemoryRepository] = None,
    trigger: Optional[SessionMemoryTrigger] = None,
) -> None:
    """Dispatch the session-memory updater. Best-effort. Never raises."""
    try:
        repo = repo or get_session_memory_repository()

        # Audit #17: route the maintenance summarizer through the agent's own
        # model (not a hardcoded Qwen) so it works off-Qwen.
        async def _summarizer(prompt: str) -> str:
            return await _default_summarizer(prompt, model=model)

        svc = SessionMemoryService(
            repo=repo,
            summarizer=_summarizer,
            trigger=trigger or SessionMemoryTrigger(),
        )
        await svc.maybe_update(session_id, messages, model=model)
    except Exception as exc:
        logger.debug(f"session_memory runner top-level swallow: {exc}")


__all__ = ["maybe_update_session_memory"]
