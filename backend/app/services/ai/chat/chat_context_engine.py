"""ChatContextEngine — adapts PromptComposer to the ContextEngine protocol.

Sprint 6.5 wire-up. Sprint 6 landed ContextEngineRegistry but no engine
was registered. This module wraps the existing chat compositor in the
ContextEngine shape so multi-surface code paths (Search, Storyboard,
Workforce) can fetch context via a uniform registry lookup instead of
import-coupling to PromptComposer.

The wrapper is thin — it forwards everything to PromptComposer.compose
and translates the rich ComposedSystemPrompt into the protocol's
generic ContextPayload envelope. ComposedSystemPrompt itself is still
returned via metadata['composed'] so callers that need the full LLM
config (model, tools, temperature, fingerprints) keep working.

Request shape this engine accepts:

    {
        "agent_slug": str,                          # required
        "request_instructions": Optional[str],
        "session_id": Optional[str],
        "model_override": Optional[str],
        "recalled_memories": list[RecalledMemory],  # default []
        "user_messages": list[dict],                # passed through verbatim
    }

Anything else in the dict is ignored — keeps the engine forward-compat
with future call-sites that want to attach extra hints.
"""

from __future__ import annotations

from typing import Any

from app.agent_framework.context_engine import ContextPayload
from app.repositories.agent_repository import AgentRepository, get_agent_repository
from app.repositories.skill_repository import (
    SkillRepository,
    get_skill_repository,
)
from app.services.ai.prompts.prompt_composer import (
    ComposerInput,
    PromptComposer,
    RecalledMemory,
)


class ChatContextEngine:
    """ContextEngine implementation backed by PromptComposer.

    Surface name: ``"chat"``. Held on app.state.context_engines after
    lifespan startup; chat callers fetch via
    ``app.state.context_engines.require("chat")``.
    """

    name: str = "chat"

    def __init__(
        self,
        composer: PromptComposer | None = None,
        *,
        agent_repo: AgentRepository | None = None,
        skill_repo: SkillRepository | None = None,
    ) -> None:
        # Allow caller to inject a pre-built composer (test seam) or
        # let us build the default one from the standard repo classes.
        if composer is None:
            composer = PromptComposer(
                agent_repo=agent_repo or get_agent_repository(),
                skill_repo=skill_repo or get_skill_repository(),
            )
        self._composer = composer

    async def assemble(self, request: dict[str, Any]) -> ContextPayload:
        """Assemble the chat context envelope.

        ``request['agent_slug']`` is required; everything else is optional.
        Raises whatever PromptComposer.compose raises (AgentNotFoundError
        for missing slug) — caller-side translation to HTTP errors stays
        in the route layer.
        """
        agent_slug = request.get("agent_slug")
        if not agent_slug:
            raise ValueError("ChatContextEngine: 'agent_slug' is required")

        recalled = request.get("recalled_memories") or []
        # Tolerate two shapes: raw RecalledMemory objects (preferred) or
        # plain dicts that look like one (some test fixtures pass dicts).
        normalized = [
            m if isinstance(m, RecalledMemory) else RecalledMemory(**m)
            for m in recalled
        ]

        composed = await self._composer.compose(
            ComposerInput(
                agent_slug=agent_slug,
                request_instructions=request.get("request_instructions"),
                session_id=request.get("session_id"),
                model_override=request.get("model_override"),
                recalled_memories=normalized,
            )
        )

        user_messages = list(request.get("user_messages") or [])

        return ContextPayload(
            system_message=composed.system_message,
            user_messages=user_messages,
            cache_fingerprint=composed.prefix_fingerprint,
            metadata={
                # Full ComposedSystemPrompt — chat callers need its fields
                # (model, tools, temperature, dynamic_fingerprint, manifest)
                # for the actual LLM call. The protocol's generic envelope
                # carries it through metadata.
                "composed": composed,
                "agent_slug": composed.agent_slug,
                "model": composed.model,
                "tools": composed.tools,
                "skill_manifest": composed.skill_manifest,
                "recalled_memory_ids": [
                    str(uid) for uid in composed.recalled_memory_ids
                ],
                "dynamic_fingerprint": composed.dynamic_fingerprint,
            },
        )


__all__ = ["ChatContextEngine"]
