"""Unit tests for MemoryRetriever — pgvector + salience + sonnet + cache.

★ P0: cross-user isolation. The retriever MUST filter by user_id everywhere.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.services.ai.memory.retriever import (
    DEFAULT_TOP_K_CANDIDATES,
    DEFAULT_TOP_N_FINAL,
    MemoryRetriever,
)


def _row(
    *,
    user_id: UUID,
    agent_id: UUID,
    summary: str = "fact",
    when: str = "when",
    cosine: float = 0.9,
    reinforcement: int = 0,
    rec_id: UUID = None,
) -> dict:
    return {
        "id": str(rec_id or uuid4()),
        "agent_id": str(agent_id),
        "user_id": str(user_id),
        "scope": "agent_user",
        "summary": summary,
        "when_to_use": when,
        "extracted_from": "user_msg",
        "reinforcement_count": reinforcement,
        "last_recalled_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata_json": {},
        "cosine_similarity": cosine,
    }


def _make_supabase(rpc_rows=None, rpc_exception=None):
    client = MagicMock()
    rpc_result = AsyncMock()
    if rpc_exception is not None:
        rpc_result.execute = AsyncMock(side_effect=rpc_exception)
    else:
        rpc_result.execute = AsyncMock(return_value=MagicMock(data=rpc_rows or []))
    client.rpc.return_value = rpc_result

    table_result = AsyncMock()
    table_result.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value.select.return_value.in_.return_value.eq.return_value = (
        table_result
    )
    return client


async def _embed_call(text):
    return [0.1] * 1536


async def _sonnet_keep_all(prompt: str) -> str:
    # Sonnet picks all candidates. Output [0], [1], [2], etc.
    candidate_count = prompt.count("(when_to_use:")
    return ", ".join(f"[{i}]" for i in range(candidate_count))


async def _sonnet_keep_first(prompt: str) -> str:
    return "[0]"


async def _sonnet_none(prompt: str) -> str:
    return "NONE"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_cosine_then_sonnet_returns_records():
    user = uuid4()
    agent = uuid4()
    rows = [
        _row(user_id=user, agent_id=agent, summary="A", cosine=0.95),
        _row(user_id=user, agent_id=agent, summary="B", cosine=0.8),
    ]
    client = _make_supabase(rpc_rows=rows)
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    result = await retriever.recall(
        user_id=user, agent_id=agent, session_id=None, user_query="hi"
    )
    assert len(result) == 2
    assert {r.summary for r in result} == {"A", "B"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_no_candidates_returns_empty():
    client = _make_supabase(rpc_rows=[])
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_sonnet_says_none_returns_empty():
    rows = [_row(user_id=uuid4(), agent_id=uuid4())]
    client = _make_supabase(rpc_rows=rows)
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_none,
        redis_client=None,
    )
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    assert result == []


# ---------------------------------------------------------------------------
# ★ P0: user isolation enforcement
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_recall_passes_user_id_to_rpc():
    """RPC must be called with the requester's user_id — RLS / DB-level guard."""
    client = _make_supabase(rpc_rows=[])
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    user = uuid4()
    agent = uuid4()
    await retriever.recall(user_id=user, agent_id=agent, session_id=None, user_query="hi")

    client.rpc.assert_called_once()
    args, kwargs = client.rpc.call_args
    assert args[0] == "recall_agent_memories"
    payload = args[1]
    assert payload["p_user_id"] == str(user)
    assert payload["p_agent_id"] == str(agent)
    assert payload["p_scope"] == "agent_user"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_cache_hit_fetch_filters_by_user_id():
    """Cache-hit path also goes through eq('user_id', ...) filter."""
    user = uuid4()
    agent = uuid4()
    cached_id = uuid4()

    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps([str(cached_id)]))
    redis.set = AsyncMock()

    client = MagicMock()
    table_result = AsyncMock()
    table_result.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value.select.return_value.in_.return_value.eq.return_value = (
        table_result
    )
    # Reinforce RPC stub
    rpc_result = AsyncMock()
    rpc_result.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.rpc.return_value = rpc_result

    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=redis,
    )
    await retriever.recall(user_id=user, agent_id=agent, session_id=uuid4(), user_query="hi")

    # Verify the .eq() was called with user_id binding.
    client.table.return_value.select.return_value.in_.return_value.eq.assert_called_with(
        "user_id", str(user)
    )


# ---------------------------------------------------------------------------
# Salience scoring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_salience_reranks_by_reinforcement():
    """High reinforcement boosts a record above a slightly higher cosine peer."""
    from app.services.ai.memory import MemoryRecord, MemoryScope

    a = MemoryRecord(
        id=uuid4(), agent_id=uuid4(), user_id=uuid4(),
        scope=MemoryScope.AGENT_USER,
        summary="frequent", when_to_use="w", extracted_from=None,
        reinforcement_count=100, last_recalled_at=None,
        created_at=datetime.now(timezone.utc),
    )
    b = MemoryRecord(
        id=uuid4(), agent_id=uuid4(), user_id=uuid4(),
        scope=MemoryScope.AGENT_USER,
        summary="rare", when_to_use="w", extracted_from=None,
        reinforcement_count=0, last_recalled_at=None,
        created_at=datetime.now(timezone.utc),
    )
    retriever = MemoryRetriever(
        supabase_client=MagicMock(),
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    # b has higher cosine but a has 100 reinforcement
    candidates = [(b, 0.85), (a, 0.80)]
    ranked = retriever._apply_salience(candidates)
    # a's salience: 0.7*0.80 + 0.3*log(101) ≈ 0.56 + 1.39 = 1.95
    # b's salience: 0.7*0.85 + 0.3*log(1) = 0.595
    assert ranked[0][0] is a


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_miss_then_set_with_ttl():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()

    rows = [_row(user_id=uuid4(), agent_id=uuid4())]
    client = _make_supabase(rpc_rows=rows)
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=redis,
        cache_ttl_s=300,
    )
    await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )

    redis.set.assert_called_once()
    _, kwargs = redis.set.call_args
    assert kwargs["ex"] == 300


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_disabled_when_redis_none():
    rows = [_row(user_id=uuid4(), agent_id=uuid4())]
    client = _make_supabase(rpc_rows=rows)
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    # Should not crash without a redis client.
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    assert len(result) == 1


@pytest.mark.unit
def test_cache_key_stable_for_same_inputs():
    user = uuid4()
    agent = uuid4()
    sess = uuid4()
    a = MemoryRetriever._build_cache_key(
        user_id=user, agent_id=agent, session_id=sess, user_query="hello"
    )
    b = MemoryRetriever._build_cache_key(
        user_id=user, agent_id=agent, session_id=sess, user_query="hello"
    )
    assert a == b


@pytest.mark.unit
def test_cache_key_changes_with_query():
    user = uuid4()
    agent = uuid4()
    sess = uuid4()
    a = MemoryRetriever._build_cache_key(
        user_id=user, agent_id=agent, session_id=sess, user_query="hello"
    )
    b = MemoryRetriever._build_cache_key(
        user_id=user, agent_id=agent, session_id=sess, user_query="goodbye"
    )
    assert a != b


@pytest.mark.unit
def test_cache_key_namespace_includes_user():
    """★ P0: different users with same query MUST have different cache keys."""
    user_a = uuid4()
    user_b = uuid4()
    agent = uuid4()
    a = MemoryRetriever._build_cache_key(
        user_id=user_a, agent_id=agent, session_id=None, user_query="hello"
    )
    b = MemoryRetriever._build_cache_key(
        user_id=user_b, agent_id=agent, session_id=None, user_query="hello"
    )
    assert a != b


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embedding_failure_returns_empty_no_recall():
    async def broken_embed(text):
        raise RuntimeError("API down")

    retriever = MemoryRetriever(
        supabase_client=_make_supabase(),
        embedding_call=broken_embed,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rpc_failure_returns_empty():
    client = _make_supabase(rpc_exception=RuntimeError("DB down"))
    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=_sonnet_keep_all,
        redis_client=None,
    )
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sonnet_failure_falls_back_to_top_n_salience():
    rows = [
        _row(user_id=uuid4(), agent_id=uuid4(), summary=f"r{i}", cosine=0.9 - i * 0.01)
        for i in range(8)
    ]
    client = _make_supabase(rpc_rows=rows)

    async def broken_sonnet(prompt):
        raise RuntimeError("LLM down")

    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=_embed_call,
        sonnet_call=broken_sonnet,
        redis_client=None,
        top_n=3,
    )
    result = await retriever.recall(
        user_id=uuid4(), agent_id=uuid4(), session_id=None, user_query="hi"
    )
    # Falls back to top-N — never raises.
    assert len(result) == 3
