"""Context-engine registry — surface-specific context assembly.

Sprint 6 primitive. Today ``services/prompt_composer.py`` is the only
context-assembly path: it takes (agent_slug, recalled_memories, request
instructions) → ComposedSystemPrompt for chat. Adding new surfaces
(Search, Storyboard, Workforce) means duplicating that orchestration.

This module defines the registry pattern. Each "surface" registers a
``ContextEngine`` implementation; callers ask the registry for the
engine by name. No engines are bundled here — the chat engine lives in
``services/prompt_composer.py`` and registers itself at startup. New
surfaces add their own engine class + register call without touching
the others.

Design borrowed from OpenClaw's context-engine pattern (TypeScript) — a
context engine knows what to look up, in what order, and how to budget
tokens for its surface. The registry just wires them up.

This is the registry primitive only. The ContextEngine *Protocol* and
ContextPayload value object are deliberately minimal — concrete engines
extend them with their own request/response shapes via narrower methods,
because the chat / search / storyboard surfaces have nothing in common
beyond "give me a payload to send to the model."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ContextPayload:
    """Generic envelope returned by every context engine.

    Concrete engines may attach surface-specific fields via ``metadata``,
    but every engine produces at minimum a system-message string and a
    list of user-side messages — the LLM call shape.
    """

    system_message: str
    user_messages: list[dict]
    cache_fingerprint: str  # SHA1 of the cacheable prefix
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ContextEngine(Protocol):
    """A surface's context assembler.

    Implementations vary widely (chat composes from agent rows + memory;
    search composes from query embedding + filters; storyboard composes
    from scene state + previous frames). The shared contract is just
    "given a request dict, produce a ContextPayload."

    Engines are typically registered as singletons at startup. They may
    cache internally but should not hold per-request state.
    """

    @property
    def name(self) -> str:
        """Surface key used for lookup. Conventionally lowercase
        snake_case (e.g. 'chat', 'search', 'storyboard')."""

    async def assemble(self, request: dict[str, Any]) -> ContextPayload:
        """Assemble the payload for one request. Engine-specific request
        shape — callers know which engine they're talking to."""


class DuplicateContextEngineError(ValueError):
    """Raised when registering a name that's already taken — surfaces
    must be uniquely keyed for the registry to be useful."""


class ContextEngineRegistry:
    """Per-process registry. Engines register themselves at startup;
    callers fetch by surface name.

    Not thread-safe; one registry per asyncio loop. Typical lifecycle:
    instantiated in FastAPI lifespan, populated immediately, then
    read-only for the rest of the process's life.
    """

    def __init__(self, *, allow_replace: bool = False) -> None:
        self._engines: dict[str, ContextEngine] = {}
        self._allow_replace = allow_replace

    def register(self, engine: ContextEngine) -> None:
        """Insert ``engine`` keyed by its ``name``. Raises if the name
        is taken (unless the registry was constructed with
        ``allow_replace=True``)."""
        name = engine.name
        if not name or not isinstance(name, str):
            raise ValueError(f"engine.name must be a non-empty str (got {name!r})")
        if name in self._engines and not self._allow_replace:
            raise DuplicateContextEngineError(
                f"context engine '{name}' is already registered"
            )
        self._engines[name] = engine

    def get(self, name: str) -> ContextEngine | None:
        """Look up an engine by name. Returns None if not registered —
        callers decide whether to raise or fall back."""
        return self._engines.get(name)

    def require(self, name: str) -> ContextEngine:
        """Like ``get`` but raises if not found. Use when the caller
        cannot proceed without the engine."""
        engine = self._engines.get(name)
        if engine is None:
            raise KeyError(
                f"no context engine registered for surface '{name}' — "
                f"available: {sorted(self._engines)}"
            )
        return engine

    def unregister(self, name: str) -> bool:
        """Remove an engine. Returns True if it existed, False otherwise."""
        return self._engines.pop(name, None) is not None

    def names(self) -> list[str]:
        """All registered surface names (sorted, for stable display)."""
        return sorted(self._engines)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._engines

    def __len__(self) -> int:
        return len(self._engines)


__all__ = [
    "ContextEngine",
    "ContextEngineRegistry",
    "ContextPayload",
    "DuplicateContextEngineError",
]
