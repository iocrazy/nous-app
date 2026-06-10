"""Tests for Graphiti fact injection into the composed prompt (Phase 4 M3)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.services.ai.memory.graph_memory import (
    GraphFact,
    GraphMemoryConfig,
    GraphMemoryService,
)
from app.services.ai.prompts.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    PromptComposer,
    RecalledMemory,
)

AGENT = {
    "id": "00000000-0000-0000-0000-000000000001",
    "slug": "tester",
    "identity_md": "I am a test agent.",
    "soul_md": "",
    "agent_md": "",
}


def _composer() -> PromptComposer:
    return PromptComposer(None, None)


# ============================================================
# Composer rendering
# ============================================================


class TestGraphFactsRendering:
    def test_facts_render_after_cache_boundary(self) -> None:
        msg = _composer()._assemble_system_message(
            agent=AGENT,
            skills=[],
            request_instructions=None,
            graph_facts=["Alice runs Studio Nine", "Studio Nine ships shorts"],
        )
        boundary = msg.index(CACHE_BOUNDARY_MARKER)
        facts_at = msg.index("<graph_facts>")
        assert facts_at > boundary
        assert "<fact>Alice runs Studio Nine</fact>" in msg
        assert "<fact>Studio Nine ships shorts</fact>" in msg

    def test_no_facts_renders_no_section(self) -> None:
        msg = _composer()._assemble_system_message(
            agent=AGENT, skills=[], request_instructions=None, graph_facts=[]
        )
        assert "<graph_facts>" not in msg

    def test_facts_are_xml_escaped(self) -> None:
        section = _composer()._render_graph_facts_section(["a <b> c"])
        assert "<fact>a &lt;b&gt; c</fact>" in section

    def test_graph_facts_after_recalled_memories(self) -> None:
        msg = _composer()._assemble_system_message(
            agent=AGENT,
            skills=[],
            request_instructions=None,
            recalled_memories=[
                RecalledMemory(id=UUID(int=1), summary="s", when_to_use="w")
            ],
            graph_facts=["fact one"],
        )
        assert msg.index("<recalled_memories>") < msg.index("<graph_facts>")


class TestDynamicFingerprint:
    def test_fact_set_changes_fingerprint(self) -> None:
        composer = _composer()
        base = composer._dynamic_fingerprint("prefix", [], [])
        with_facts = composer._dynamic_fingerprint("prefix", [], ["a fact"])
        assert base != with_facts

    def test_fact_order_does_not_change_fingerprint(self) -> None:
        composer = _composer()
        ab = composer._dynamic_fingerprint("prefix", [], ["a", "b"])
        ba = composer._dynamic_fingerprint("prefix", [], ["b", "a"])
        assert ab == ba


# ============================================================
# Wiring recall helper
# ============================================================


class FakeService(GraphMemoryService):
    def __init__(self, *, enabled: bool, facts=None, raise_=False):
        super().__init__(config=GraphMemoryConfig(enabled=enabled, falkordb_host="h"))
        self._facts = facts or []
        self._raise = raise_
        self.queries: list[dict] = []

    async def search(self, query, *, group_id, limit=10):  # type: ignore[override]
        if self._raise:
            raise RuntimeError("boom")
        self.queries.append({"query": query, "group_id": group_id, "limit": limit})
        return self._facts


@pytest.mark.asyncio
async def test_recall_maps_facts_and_scopes_to_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    svc = FakeService(enabled=True, facts=[GraphFact(fact="likes vertical video")])
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=42), user_query="export size?"
    )
    assert facts == ["likes vertical video"]
    assert svc.queries[0]["group_id"] == f"user-{UUID(int=42)}"
    assert svc.queries[0]["limit"] == 5


@pytest.mark.asyncio
async def test_recall_flag_off_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    svc = FakeService(enabled=False)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=42), user_query="anything"
    )
    assert facts == []
    assert svc.queries == []


@pytest.mark.asyncio
async def test_recall_failure_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    svc = FakeService(enabled=True, raise_=True)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=42), user_query="anything"
    )
    assert facts == []
