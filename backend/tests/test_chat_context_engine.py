"""Sprint 6.5 — ChatContextEngine adapter."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.agent_framework.context_engine import ContextEngine, ContextPayload
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.chat_context_engine import ChatContextEngine
from app.services.prompt_composer import ComposerInput, RecalledMemory


_AGENT_ID = uuid4()


class _FakeComposer:
    """Stand-in for PromptComposer — captures the ComposerInput it
    received so tests can assert on the translation."""

    def __init__(self) -> None:
        self.received: ComposerInput | None = None

    async def compose(self, inp: ComposerInput) -> ComposedSystemPrompt:
        self.received = inp
        return ComposedSystemPrompt(
            agent_id=_AGENT_ID,
            agent_slug=inp.agent_slug,
            model=inp.model_override or "qwen-max",
            temperature=0.7,
            max_tokens=4096,
            system_message="SYSTEM: " + inp.agent_slug,
            tools=[{"type": "function", "function": {"name": "x"}}],
            skill_manifest=[{"slug": "s1", "name": "S1", "description": "d"}],
            cache_fingerprint="prefix-fp",
            prefix_fingerprint="prefix-fp",
            dynamic_fingerprint="dynamic-fp",
            recalled_memory_ids=[m.id for m in inp.recalled_memories],
        )


@pytest.fixture
def engine():
    return ChatContextEngine(composer=_FakeComposer())


# ─── Protocol compliance ──────────────────────────────────────────────


@pytest.mark.unit
def test_engine_satisfies_protocol(engine):
    assert isinstance(engine, ContextEngine)
    assert engine.name == "chat"


# ─── assemble() happy path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assemble_returns_payload_with_composed_in_metadata(engine):
    payload = await engine.assemble({"agent_slug": "script_ai"})
    assert isinstance(payload, ContextPayload)
    assert payload.system_message == "SYSTEM: script_ai"
    assert payload.user_messages == []
    assert payload.cache_fingerprint == "prefix-fp"
    # Caller still gets the rich ComposedSystemPrompt for the LLM call
    assert isinstance(payload.metadata["composed"], ComposedSystemPrompt)
    assert payload.metadata["agent_slug"] == "script_ai"
    assert payload.metadata["model"] == "qwen-max"
    assert payload.metadata["dynamic_fingerprint"] == "dynamic-fp"


@pytest.mark.asyncio
async def test_assemble_passes_user_messages_through(engine):
    msgs = [{"role": "user", "content": "hi"}]
    payload = await engine.assemble(
        {"agent_slug": "x", "user_messages": msgs}
    )
    assert payload.user_messages == msgs
    # Defensive copy — engine's caller can mutate without affecting input
    assert payload.user_messages is not msgs


@pytest.mark.asyncio
async def test_assemble_forwards_optional_fields(engine):
    composer: _FakeComposer = engine._composer  # noqa: SLF001
    await engine.assemble(
        {
            "agent_slug": "x",
            "request_instructions": "be brief",
            "session_id": "sess-1",
            "model_override": "qwen-plus",
        }
    )
    assert composer.received is not None
    assert composer.received.request_instructions == "be brief"
    assert composer.received.session_id == "sess-1"
    assert composer.received.model_override == "qwen-plus"


@pytest.mark.asyncio
async def test_assemble_normalizes_recalled_memory_dicts(engine):
    """Tolerate dict-shaped memories (test fixtures + admin tools)."""
    mem_id = uuid4()
    payload = await engine.assemble(
        {
            "agent_slug": "x",
            "recalled_memories": [
                {"id": mem_id, "summary": "user is heygo", "when_to_use": "any"}
            ],
        }
    )
    assert payload.metadata["recalled_memory_ids"] == [str(mem_id)]


@pytest.mark.asyncio
async def test_assemble_accepts_recalled_memory_objects(engine):
    mem = RecalledMemory(id=uuid4(), summary="x", when_to_use="y")
    composer: _FakeComposer = engine._composer  # noqa: SLF001
    await engine.assemble(
        {"agent_slug": "x", "recalled_memories": [mem]}
    )
    assert composer.received.recalled_memories == [mem]


# ─── Error paths ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assemble_missing_agent_slug_raises(engine):
    with pytest.raises(ValueError, match="agent_slug"):
        await engine.assemble({})


@pytest.mark.asyncio
async def test_assemble_empty_agent_slug_raises(engine):
    with pytest.raises(ValueError, match="agent_slug"):
        await engine.assemble({"agent_slug": ""})


# ─── Registry integration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_in_context_engine_registry():
    """End-to-end: register, fetch by name, assemble."""
    from app.agent_framework import ContextEngineRegistry

    reg = ContextEngineRegistry()
    reg.register(ChatContextEngine(composer=_FakeComposer()))
    fetched = reg.require("chat")
    payload = await fetched.assemble({"agent_slug": "script_ai"})
    assert payload.metadata["agent_slug"] == "script_ai"
