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
from app.repositories.session_memory_repository import SessionMemoryRepository


# Cheap-summarizer prompt routed through the same OpenAI-compatible
# adapter the chat compactor uses. Kept here (not in agent_framework) to
# avoid pulling settings imports into a primitive layer.
async def _default_summarizer(prompt: str) -> str:
    """Cheap LLM call for session-memory maintenance. Routes through the
    qwen-flash / qwen-turbo adapter — same one chat compactor uses."""
    try:
        from app.services.ai.providers.ai_provider import QwenAdapter
        from app.core.config import settings

        api_key = getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(
            settings, "QWEN_API_KEY", None
        )
        if not api_key:
            logger.debug("session_memory summarizer: no Qwen key — skipping")
            return ""
        adapter = QwenAdapter(api_key=api_key, model="qwen-turbo")
        # Minimal "messages" shape the adapter accepts.
        from app.schemas.ai_library import ComposedSystemPrompt
        composed = ComposedSystemPrompt(
            agent_id=None,  # type: ignore[arg-type]  — runner doesn't need it
            agent_slug="session_memory_updater",
            model="qwen-turbo",
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
        repo = repo or SessionMemoryRepository()
        svc = SessionMemoryService(
            repo=repo,
            summarizer=_default_summarizer,
            trigger=trigger or SessionMemoryTrigger(),
        )
        await svc.maybe_update(session_id, messages, model=model)
    except Exception as exc:
        logger.debug(f"session_memory runner top-level swallow: {exc}")


__all__ = ["maybe_update_session_memory"]
