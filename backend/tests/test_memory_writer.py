"""Unit tests for MemoryWriter — extract → embed → insert."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.memory import ExtractedFrom, MemoryScope
from app.services.memory.extractor import (
    AssistantMemoryExtractor,
    ExtractedFact,
    UserMemoryExtractor,
)
from app.services.memory.writer import MemoryWriter


def _make_supabase():
    """Mock client supporting client.table('agent_memories').insert(rows).execute()."""
    client = MagicMock()
    insert_result = AsyncMock()
    insert_result.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value.insert.return_value = insert_result
    return client


class _StubUserExtractor:
    def __init__(self, facts):
        self._facts = facts

    async def extract(self, _msgs):
        return self._facts


class _StubAssistantExtractor:
    def __init__(self, facts):
        self._facts = facts

    async def extract(self, _msgs):
        return self._facts


def _make_extractor(facts):
    return _StubUserExtractor(facts)


def _make_assistant_extractor(facts):
    return _StubAssistantExtractor(facts)


def _make_embedder(vector):
    embedder = MagicMock()
    embedder.generate_embedding = AsyncMock(return_value=vector)
    return embedder


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_skips_when_no_facts_extracted():
    writer = MemoryWriter(
        user_extractor=_make_extractor([]),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder([0.1] * 1536),
        supabase_client=_make_supabase(),
    )
    rows = await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=[], assistant_messages=[],
    )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_inserts_one_row_per_fact():
    user_facts = [
        ExtractedFact(summary="user fact 1", when_to_use="why 1", extracted_from=ExtractedFrom.USER_MSG),
    ]
    asst_facts = [
        ExtractedFact(summary="asst fact 1", when_to_use="why a", extracted_from=ExtractedFrom.ASSISTANT_MSG),
    ]
    client = _make_supabase()

    writer = MemoryWriter(
        user_extractor=_make_extractor(user_facts),
        assistant_extractor=_make_assistant_extractor(asst_facts),
        embedding_service=_make_embedder([0.2] * 1536),
        supabase_client=client,
    )
    user_id = uuid4()
    agent_id = uuid4()
    rows = await writer.write(
        agent_id=agent_id, user_id=user_id, run_id=None,
        user_messages=["x"], assistant_messages=["y"],
    )

    assert rows == 2
    insert_call = client.table.return_value.insert
    insert_call.assert_called_once()
    payload = insert_call.call_args.args[0]
    assert len(payload) == 2
    # All rows tagged with user_id (P0 isolation)
    assert all(row["user_id"] == str(user_id) for row in payload)
    # Embedding vector populated
    assert all(len(row["embedding"]) == 1536 for row in payload)
    # Scope defaulted to agent_user (M1.B writes only this layer)
    assert all(row["scope"] == "agent_user" for row in payload)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_drops_facts_with_no_embedding():
    facts = [ExtractedFact("s", "w", ExtractedFrom.USER_MSG)]
    client = _make_supabase()

    embedder = MagicMock()
    embedder.generate_embedding = AsyncMock(return_value=None)  # no API key

    writer = MemoryWriter(
        user_extractor=_make_extractor(facts),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=embedder,
        supabase_client=client,
    )
    rows = await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=[],
    )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_db_failure_returns_zero():
    facts = [ExtractedFact("s", "w", ExtractedFrom.USER_MSG)]
    client = MagicMock()
    insert_result = AsyncMock()
    insert_result.execute = AsyncMock(side_effect=RuntimeError("DB down"))
    client.table.return_value.insert.return_value = insert_result

    writer = MemoryWriter(
        user_extractor=_make_extractor(facts),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder([0.1] * 1536),
        supabase_client=client,
    )
    rows = await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=[],
    )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_preserves_extracted_from_per_fact():
    user_fact = ExtractedFact("u", "uw", ExtractedFrom.USER_MSG)
    asst_fact = ExtractedFact("a", "aw", ExtractedFrom.ASSISTANT_MSG)
    client = _make_supabase()

    writer = MemoryWriter(
        user_extractor=_make_extractor([user_fact]),
        assistant_extractor=_make_assistant_extractor([asst_fact]),
        embedding_service=_make_embedder([0.1] * 1536),
        supabase_client=client,
    )
    await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=["y"],
    )
    payload = client.table.return_value.insert.call_args.args[0]
    sources = {row["extracted_from"] for row in payload}
    assert sources == {"user_msg", "assistant_msg"}


# ───────────────────────────────────────────────────────────────────
# Wave F (F3): contradiction post-pass
# ───────────────────────────────────────────────────────────────────


def _make_supabase_with_returning(insert_returns):
    """Like _make_supabase but the insert RETURNS rows (with id field)
    so the writer's post-pass can scan them."""
    client = MagicMock()
    insert_result = AsyncMock()
    insert_result.execute = AsyncMock(return_value=MagicMock(data=insert_returns))
    client.table.return_value.insert.return_value = insert_result

    # neighbors query: select().eq().eq()...neq().limit().execute()
    select_chain = MagicMock()
    select_chain.eq.return_value = select_chain
    select_chain.neq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value.select.return_value = select_chain

    # update path
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value.update.return_value = update_chain
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contradiction_classifier_runs_post_insert_when_set():
    """Classifier called for each high-similarity neighbor, REPLACES
    triggers UPDATE with status=superseded."""
    fact = ExtractedFact(
        summary="user uses React",
        when_to_use="when discussing frontend",
        extracted_from=ExtractedFrom.USER_MSG,
    )
    embedding = [0.1] * 16

    insert_returns = [
        {
            "id": "new-uuid-1",
            "summary": "user uses React",
            "embedding": embedding,
        }
    ]
    client = _make_supabase_with_returning(insert_returns)

    # Stub the neighbor query to return one HIGH-similarity old memory
    old_neighbor = {
        "id": "old-uuid-1",
        "summary": "user uses Vue",
        "embedding": embedding,  # same vec → cosine = 1.0
    }
    client.table.return_value.select.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[old_neighbor])
    )

    classifier_calls = []

    async def _classifier(prompt):
        classifier_calls.append(prompt)
        return "replaces"

    writer = MemoryWriter(
        user_extractor=_make_extractor([fact]),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder(embedding),
        supabase_client=client,
        contradiction_classifier=_classifier,
    )
    n = await writer.write(
        agent_id=uuid4(),
        user_id=uuid4(),
        run_id=None,
        user_messages=["I now use React"],
        assistant_messages=[],
    )
    assert n == 1
    # Classifier consulted at least once
    assert len(classifier_calls) >= 1
    # UPDATE was called with status=superseded
    update_payload = client.table.return_value.update.call_args.args[0]
    assert update_payload["status"] == "superseded"
    assert update_payload["superseded_by"] == "new-uuid-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_classifier_skips_post_pass():
    """Default behavior: no classifier set → skip post-pass entirely."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )
    client = _make_supabase_with_returning([{"id": "1", "summary": "x", "embedding": [0.1]}])
    writer = MemoryWriter(
        user_extractor=_make_extractor([fact]),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder([0.1]),
        supabase_client=client,
        # contradiction_classifier=None (default)
    )
    await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=[],
    )
    # No update calls (post-pass not invoked)
    client.table.return_value.update.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classifier_unrelated_does_not_supersede():
    """LLM says UNRELATED → keep both memories (no UPDATE)."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )
    embedding = [0.1] * 8
    client = _make_supabase_with_returning(
        [{"id": "new-1", "summary": "x", "embedding": embedding}]
    )
    client.table.return_value.select.return_value.execute = AsyncMock(
        return_value=MagicMock(
            data=[{"id": "old-1", "summary": "different", "embedding": embedding}]
        )
    )

    async def _classifier(prompt):
        return "unrelated"

    writer = MemoryWriter(
        user_extractor=_make_extractor([fact]),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder(embedding),
        supabase_client=client,
        contradiction_classifier=_classifier,
    )
    await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=[],
    )
    # No UPDATE — neighbors examined but verdict = keep
    client.table.return_value.update.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contradiction_post_pass_failure_doesnt_break_insert():
    """Post-pass exception → log + continue, write still succeeds."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )
    client = _make_supabase_with_returning([{"id": "1", "summary": "x", "embedding": [0.1]}])

    # Make the SELECT for neighbors blow up
    client.table.return_value.select.return_value.execute = AsyncMock(
        side_effect=RuntimeError("db down")
    )

    async def _classifier(prompt):
        return "replaces"

    writer = MemoryWriter(
        user_extractor=_make_extractor([fact]),
        assistant_extractor=_make_assistant_extractor([]),
        embedding_service=_make_embedder([0.1]),
        supabase_client=client,
        contradiction_classifier=_classifier,
    )
    n = await writer.write(
        agent_id=uuid4(), user_id=uuid4(), run_id=None,
        user_messages=["x"], assistant_messages=[],
    )
    # Insert still counted as success
    assert n == 1
