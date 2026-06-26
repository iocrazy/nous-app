"""Agent memory scoped recall (Phase A) — isolation predicate + ranking."""

from unittest.mock import patch

import pytest

from app.services.ai.memory.agent_memory import MemoryContext, recall


@pytest.mark.asyncio
async def test_recall_returns_empty_on_blank_query():
    ctx = MemoryContext(user_id="u1")
    assert await recall(ctx, "   ") == []


@pytest.mark.asyncio
async def test_recall_builds_scoped_query_and_maps_hits():
    # Capture the SQL + params the repo runs; return one fake row.
    captured = {}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "id": 7,
                    "title": "deploy",
                    "body_md": "run x",
                    "kind": "fact",
                    "score": 0.9,
                }
            ]

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    ctx = MemoryContext(user_id="u1", team_ids=(10, 20))
    with patch(
        "app.repositories.agent_memory_repository.read_scope", return_value=_Scope()
    ):
        hits = await recall(ctx, "deploy backend", limit=5)

    assert len(hits) == 1
    assert hits[0].id == 7 and hits[0].kind == "fact"
    # isolation: the query binds the caller's user_id + team_ids
    assert captured["params"]["user_id"] == "u1"
    assert captured["params"]["team_ids"] == [10, 20]
    # uses websearch_to_tsquery + ts_rank (ranked FTS, not ILIKE)
    assert "websearch_to_tsquery" in captured["sql"]
    assert "ts_rank" in captured["sql"]


@pytest.mark.asyncio
async def test_recall_swallows_errors():
    ctx = MemoryContext(user_id="u1")
    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        side_effect=RuntimeError("db down"),
    ):
        assert await recall(ctx, "anything") == []
