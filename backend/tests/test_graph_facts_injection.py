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


class TestDynamicFingerprint:
    def test_fact_set_changes_fingerprint(self) -> None:
        composer = _composer()
        base = composer._dynamic_fingerprint("prefix", [])
        with_facts = composer._dynamic_fingerprint("prefix", ["a fact"])
        assert base != with_facts

    def test_fact_order_does_not_change_fingerprint(self) -> None:
        composer = _composer()
        ab = composer._dynamic_fingerprint("prefix", ["a", "b"])
        ba = composer._dynamic_fingerprint("prefix", ["b", "a"])
        assert ab == ba


# ============================================================
# Wiring recall helper
# ============================================================


class FakeService(GraphMemoryService):
    def __init__(self, *, enabled: bool, facts=None, raise_=False):
        super().__init__(config=GraphMemoryConfig(enabled=enabled, falkordb_host="h"))
        # Treat the injected config as already DB-resolved so the real
        # is_enabled()/_ensure_config gate honours it without a settings read.
        self._config_loaded = True
        self._facts = facts or []
        self._raise = raise_
        self.queries: list[dict] = []

    async def search(self, query, *, group_ids, limit=10):  # type: ignore[override]
        if self._raise:
            raise RuntimeError("boom")
        self.queries.append({"query": query, "group_ids": group_ids, "limit": limit})
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
    assert svc.queries[0]["group_ids"] == [f"user-{UUID(int=42)}"]
    assert svc.queries[0]["limit"] == 5


@pytest.mark.asyncio
async def test_recall_adds_project_group_for_project_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    svc = FakeService(enabled=True, facts=[GraphFact(fact="hero wears red")])
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )

    async def fake_resolve(session_id):
        assert session_id == "888"
        return "777"

    monkeypatch.setattr(wiring, "_resolve_session_project", fake_resolve)
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=42), user_query="who is the hero?", session_id="888"
    )
    assert facts == ["hero wears red"]
    assert svc.queries[0]["group_ids"] == [f"user-{UUID(int=42)}", "project-777"]


@pytest.mark.asyncio
async def test_resolve_session_project_rejects_non_numeric_ids() -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    # BIGINT column — non-numeric ids must short-circuit before the DB.
    assert await wiring._resolve_session_project(None) is None
    assert await wiring._resolve_session_project("") is None
    assert await wiring._resolve_session_project("sess-abc") is None


@pytest.mark.asyncio
async def test_resolve_session_project_binds_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        return {"project_id": 777}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    assert await wiring._resolve_session_project("888") == "777"
    assert calls[0]["params"] == {"sid": 888}


@pytest.mark.asyncio
async def test_resolve_session_project_tries_conversations_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 6 / S7: conversations-first lookup — a row found on
    ``conversations`` is used directly, no ai_sessions fallback query at all."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        return {"project_id": 42}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    assert await wiring._resolve_session_project("888") == "42"
    assert len(calls) == 1
    assert "public.conversations" in calls[0]["sql"]
    assert calls[0]["params"] == {"sid": 888}


@pytest.mark.asyncio
async def test_resolve_session_project_falls_back_to_ai_sessions_when_no_conversations_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 6 / S7: a missing conversations row (legacy session id) falls
    back to the existing ai_sessions lookup."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        if "public.conversations" in sql:
            return None
        return {"project_id": 777}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    assert await wiring._resolve_session_project("888") == "777"
    assert len(calls) == 2
    assert "public.conversations" in calls[0]["sql"]
    assert "public.ai_sessions" in calls[1]["sql"]
    assert calls[1]["params"] == {"sid": 888}


@pytest.mark.asyncio
async def test_resolve_session_project_conversations_row_with_null_project_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 6 / S7: a FOUND conversations row with project_id=NULL is
    authoritative (real "no project" answer) — must NOT fall back to
    ai_sessions."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        return {"project_id": None}

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    assert await wiring._resolve_session_project("888") is None
    assert len(calls) == 1
    assert "public.conversations" in calls[0]["sql"]


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


@pytest.mark.asyncio
async def test_recall_honors_admin_panel_toggle_over_env_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the recall gate must consult the DB-sourced (admin-panel)
    config, not the cheap env default. A service whose env default is
    disabled but whose system_settings say enabled must still recall —
    otherwise the Memory panel toggle is inert unless FEATURE_GRAPH_MEMORY
    is also set in the environment."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory import graph_memory as gm

    captured: list[dict] = []

    class _Edge:
        fact = "recalled via DB toggle"
        valid_at = None

    class _Client:
        async def search(self, query, group_ids=None, num_results=10):
            captured.append({"query": query, "group_ids": group_ids})
            return [_Edge()]

    class _DbToggledService(GraphMemoryService):
        """Env default disabled + no injected client → the real
        is_enabled()/_ensure_config path runs from_settings; we only stub the
        per-group client builder so search doesn't dial a real FalkorDB."""

        def _build_client(self, *, database):  # type: ignore[override]
            return _Client() if self.config.enabled else None

    svc = _DbToggledService(config=GraphMemoryConfig(enabled=False))

    async def fake_from_settings(**_kw):
        return GraphMemoryConfig(enabled=True, falkordb_host="db.test")

    monkeypatch.setattr(gm.GraphMemoryConfig, "from_settings", fake_from_settings)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=7), user_query="what do I like?"
    )
    assert facts == ["recalled via DB toggle"]
    assert captured  # the DB-enabled gate let the search through


@pytest.mark.asyncio
async def test_recall_suppressed_when_inject_pref_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-user 'inject memory' toggle suppresses graph fact recall too,
    mirroring the Honcho read gate — opting out must hide L3 facts, not just
    the L2 user model. The search must never even be issued."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory import graph_memory as gm
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    class _Svc:
        async def is_enabled(self) -> bool:
            return True

        async def search(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("search must not run when inject pref is off")

    monkeypatch.setattr(gm, "get_graph_memory_service", lambda: _Svc())

    async def _prefs(_uid: str) -> MemoryPrefs:
        return MemoryPrefs(learn=True, inject=False)

    monkeypatch.setattr("app.services.ai.memory.memory_prefs.get_memory_prefs", _prefs)
    facts = await wiring._safe_recall_graph_facts(
        user_id=UUID(int=7), user_query="what do I like?"
    )
    assert facts == []
