"""Tests for Graphiti fact injection into the composed prompt (Phase 4 M3)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.memory.graph_memory import (
    GraphFact,
    GraphMemoryConfig,
    GraphMemoryService,
)
from app.services.ai.prompts.prompt_composer import (
    CACHE_BOUNDARY_MARKER,
    PromptComposer,
)


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:  # noqa: D102
        return self._row


class _RecordingSession:
    def __init__(self, row: Any) -> None:
        self._result = _FakeResult(row)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any) -> Any:  # noqa: D102
        self.calls.append(_compile(stmt))
        return self._result


def _install_read_scope_row(
    monkeypatch: pytest.MonkeyPatch, *, row: Any
) -> tuple[_RecordingSession, list[tuple[str, dict]]]:
    """Patch app.db.session.read_scope to yield a session whose single
    execute() returns *row* (a plain tuple, matching a single-column SELECT,
    or None for "missing"). Used by the _resolve_session_project tests
    (ai_library_chat_wiring.py, Phase B4 ORM conversion)."""
    import app.db.session as db_session

    session = _RecordingSession(row)

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)
    return session, session.calls


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
    """Conversations-only lookup (Task 6 collapsed the compatibility layer —
    the legacy ai_sessions fallback this used to try is gone).

    ORM (Phase B4): the raw fetch_one became
    ``select(Conversations.project_id).where(...)`` through
    ``app.db.session.read_scope()``.
    """
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    session, calls = _install_read_scope_row(monkeypatch, row=(777,))
    assert await wiring._resolve_session_project("888") == "777"
    assert len(calls) == 1
    sql, binds = calls[0]
    assert "public.conversations" in sql
    assert binds == {"id_1": 888}


@pytest.mark.asyncio
async def test_resolve_session_project_returns_none_when_null_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A found conversations row with project_id=NULL is a real "no
    project" answer."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    session, calls = _install_read_scope_row(monkeypatch, row=(None,))
    assert await wiring._resolve_session_project("888") is None
    assert len(calls) == 1
    assert "public.conversations" in calls[0][0]


@pytest.mark.asyncio
async def test_resolve_session_project_returns_none_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    _install_read_scope_row(monkeypatch, row=None)
    assert await wiring._resolve_session_project("888") is None


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
