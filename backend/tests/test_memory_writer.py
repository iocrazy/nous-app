"""Unit tests for MemoryWriter — extract → embed → insert."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.memory import ExtractedFrom
from app.services.ai.memory.extractor import (
    ExtractedFact,
)
from app.services.ai.memory.writer import MemoryWriter

# ── Engine fake helpers ──────────────────────────────────────────────────────


def _make_engine_fakes(insert_rows: list[dict] | None = None):
    """Return a dict of async fake callables to patch app.db.engine helpers.

    * execute_returning_one — echoes the insert, returning a row dict that
      includes at least id, summary, and the embedding text literal.
    * fetch_all              — returns [] (thread-fetch + nearest-existing empty).
    * execute                — returns 1 (UPDATE rowcount).
    """
    _counter = [0]

    async def _fake_execute_returning_one(sql: str, params: dict) -> dict | None:
        if insert_rows is not None and _counter[0] < len(insert_rows):
            row = insert_rows[_counter[0]]
            _counter[0] += 1
            return row
        _counter[0] += 1
        # Build a minimal echo row from the INSERT params.
        emb_raw = params.get("embedding", "[0.0]")
        return {
            "id": f"mem-{_counter[0]}",
            "summary": params.get("summary", ""),
            "embedding": emb_raw,
        }

    async def _fake_fetch_all(sql: str, params: dict | None = None) -> list[dict]:
        return []

    async def _fake_execute(sql: str, params: dict | None = None) -> int:
        return 1

    return {
        "execute_returning_one": _fake_execute_returning_one,
        "fetch_all": _fake_fetch_all,
        "execute": _fake_execute,
    }


# Context manager that patches all three engine helpers at once.
def _patch_engine(fakes: dict):
    return (
        patch(
            "app.db.engine.execute_returning_one", new=fakes["execute_returning_one"]
        ),
        patch("app.db.engine.fetch_all", new=fakes["fetch_all"]),
        patch("app.db.engine.execute", new=fakes["execute"]),
    )


# ── Stub extractors / embedder ───────────────────────────────────────────────


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


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_skips_when_no_facts_extracted():
    fakes = _make_engine_fakes()
    p1, p2, p3 = _patch_engine(fakes)
    with p1, p2, p3:
        writer = MemoryWriter(
            user_extractor=_make_extractor([]),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder([0.1] * 1536),
        )
        rows = await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=[],
            assistant_messages=[],
        )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_inserts_one_row_per_fact():
    user_facts = [
        ExtractedFact(
            summary="user fact 1",
            when_to_use="why 1",
            extracted_from=ExtractedFrom.USER_MSG,
        ),
    ]
    asst_facts = [
        ExtractedFact(
            summary="asst fact 1",
            when_to_use="why a",
            extracted_from=ExtractedFrom.ASSISTANT_MSG,
        ),
    ]

    # Capture the params each INSERT call receives.
    insert_calls: list[dict] = []

    async def _capturing_insert(sql: str, params: dict) -> dict | None:
        insert_calls.append(params)
        return {
            "id": f"mem-{len(insert_calls)}",
            "summary": params.get("summary", ""),
            "embedding": params.get("embedding", "[0.0]"),
        }

    user_id = uuid4()
    agent_id = uuid4()

    with (
        patch("app.db.engine.execute_returning_one", new=_capturing_insert),
        patch("app.db.engine.fetch_all", new=AsyncMock(return_value=[])),
        patch("app.db.engine.execute", new=AsyncMock(return_value=1)),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor(user_facts),
            assistant_extractor=_make_assistant_extractor(asst_facts),
            embedding_service=_make_embedder([0.2] * 1536),
        )
        rows = await writer.write(
            agent_id=agent_id,
            user_id=user_id,
            run_id=None,
            user_messages=["x"],
            assistant_messages=["y"],
        )

    assert rows == 2
    assert len(insert_calls) == 2
    # All rows tagged with user_id (P0 isolation)
    assert all(p["user_id"] == str(user_id) for p in insert_calls)
    # Scope defaulted to agent_user (M1.B writes only this layer)
    assert all(p["scope"] == "agent_user" for p in insert_calls)
    # Embedding param is a text literal (the CAST(:embedding AS vector) form)
    assert all(isinstance(p["embedding"], str) for p in insert_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_drops_facts_with_no_embedding():
    facts = [ExtractedFact("s", "w", ExtractedFrom.USER_MSG)]
    embedder = MagicMock()
    embedder.generate_embedding = AsyncMock(return_value=None)  # no API key

    fakes = _make_engine_fakes()
    p1, p2, p3 = _patch_engine(fakes)
    with p1, p2, p3:
        writer = MemoryWriter(
            user_extractor=_make_extractor(facts),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=embedder,
        )
        rows = await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=[],
        )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_db_failure_returns_zero():
    """When execute_returning_one always raises, write() returns 0."""
    facts = [ExtractedFact("s", "w", ExtractedFrom.USER_MSG)]

    async def _failing_insert(sql: str, params: dict) -> dict | None:
        raise RuntimeError("DB down")

    with (
        patch("app.db.engine.execute_returning_one", new=_failing_insert),
        patch("app.db.engine.fetch_all", new=AsyncMock(return_value=[])),
        patch("app.db.engine.execute", new=AsyncMock(return_value=1)),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor(facts),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder([0.1] * 1536),
        )
        rows = await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=[],
        )
    assert rows == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_writer_preserves_extracted_from_per_fact():
    user_fact = ExtractedFact("u", "uw", ExtractedFrom.USER_MSG)
    asst_fact = ExtractedFact("a", "aw", ExtractedFrom.ASSISTANT_MSG)

    insert_calls: list[dict] = []

    async def _capturing_insert(sql: str, params: dict) -> dict | None:
        insert_calls.append(params)
        return {
            "id": f"m-{len(insert_calls)}",
            "summary": params.get("summary", ""),
            "embedding": "[0.1]",
        }

    with (
        patch("app.db.engine.execute_returning_one", new=_capturing_insert),
        patch("app.db.engine.fetch_all", new=AsyncMock(return_value=[])),
        patch("app.db.engine.execute", new=AsyncMock(return_value=1)),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor([user_fact]),
            assistant_extractor=_make_assistant_extractor([asst_fact]),
            embedding_service=_make_embedder([0.1] * 1536),
        )
        await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=["y"],
        )
    sources = {p["extracted_from"] for p in insert_calls}
    assert sources == {"user_msg", "assistant_msg"}


# ───────────────────────────────────────────────────────────────────
# Wave F (F3): contradiction post-pass
# ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contradiction_classifier_runs_post_insert_when_set():
    """Classifier called for each high-similarity neighbor; REPLACES
    triggers UPDATE (engine.execute) with status=superseded."""
    fact = ExtractedFact(
        summary="user uses React",
        when_to_use="when discussing frontend",
        extracted_from=ExtractedFrom.USER_MSG,
    )
    embedding = [0.1] * 16
    emb_str = "[" + ",".join(repr(x) for x in embedding) + "]"

    # INSERT returns a row with the embedding as a text literal (RETURNING *)
    async def _insert(sql: str, params: dict) -> dict | None:
        return {"id": "new-uuid-1", "summary": "user uses React", "embedding": emb_str}

    # Neighbors fetch returns one high-similarity old memory
    old_neighbor = {
        "id": "old-uuid-1",
        "summary": "user uses Vue",
        "embedding": emb_str,
    }

    async def _fetch_all(sql: str, params: dict | None = None) -> list[dict]:
        # First call is thread_id fetch (session_id=None → not called here),
        # subsequent calls are the _nearest_existing fetch.
        return [old_neighbor]

    update_calls: list[dict] = []

    async def _execute(sql: str, params: dict | None = None) -> int:
        update_calls.append(params or {})
        return 1

    classifier_calls: list[str] = []

    async def _classifier(prompt: str) -> str:
        classifier_calls.append(prompt)
        return "replaces"

    with (
        patch("app.db.engine.execute_returning_one", new=_insert),
        patch("app.db.engine.fetch_all", new=_fetch_all),
        patch("app.db.engine.execute", new=_execute),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor([fact]),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder(embedding),
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
    # UPDATE was called with status=superseded and correct new id
    assert len(update_calls) >= 1
    assert update_calls[0].get("new") == "new-uuid-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_classifier_skips_post_pass():
    """Default behavior: no classifier set → skip post-pass entirely
    (engine.execute for UPDATE never called)."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )

    execute_calls: list[str] = []

    async def _execute(sql: str, params: dict | None = None) -> int:
        execute_calls.append(sql)
        return 1

    async def _insert(sql: str, params: dict) -> dict | None:
        return {"id": "1", "summary": "x", "embedding": "[0.1]"}

    with (
        patch("app.db.engine.execute_returning_one", new=_insert),
        patch("app.db.engine.fetch_all", new=AsyncMock(return_value=[])),
        patch("app.db.engine.execute", new=_execute),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor([fact]),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder([0.1]),
            # contradiction_classifier=None (default)
        )
        await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=[],
        )
    # No UPDATE calls (post-pass not invoked)
    assert not any("UPDATE" in s for s in execute_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classifier_unrelated_does_not_supersede():
    """LLM says UNRELATED → keep both memories (no UPDATE)."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )
    embedding = [0.1] * 8
    emb_str = "[" + ",".join(repr(x) for x in embedding) + "]"

    async def _insert(sql: str, params: dict) -> dict | None:
        return {"id": "new-1", "summary": "x", "embedding": emb_str}

    old_neighbor = {"id": "old-1", "summary": "different", "embedding": emb_str}

    async def _fetch_all(sql: str, params: dict | None = None) -> list[dict]:
        return [old_neighbor]

    update_calls: list[str] = []

    async def _execute(sql: str, params: dict | None = None) -> int:
        update_calls.append(sql)
        return 1

    async def _classifier(prompt: str) -> str:
        return "unrelated"

    with (
        patch("app.db.engine.execute_returning_one", new=_insert),
        patch("app.db.engine.fetch_all", new=_fetch_all),
        patch("app.db.engine.execute", new=_execute),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor([fact]),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder(embedding),
            contradiction_classifier=_classifier,
        )
        await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=[],
        )
    # No UPDATE — neighbors examined but verdict = keep
    assert not any("UPDATE" in s for s in update_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contradiction_post_pass_failure_doesnt_break_insert():
    """Post-pass exception (fetch_all raises) → log + continue, write still succeeds."""
    fact = ExtractedFact(
        summary="x", when_to_use="y", extracted_from=ExtractedFrom.USER_MSG
    )

    async def _insert(sql: str, params: dict) -> dict | None:
        return {"id": "1", "summary": "x", "embedding": "[0.1]"}

    async def _fetch_all_error(sql: str, params: dict | None = None) -> list[dict]:
        raise RuntimeError("db down")

    async def _classifier(prompt: str) -> str:
        return "replaces"

    with (
        patch("app.db.engine.execute_returning_one", new=_insert),
        patch("app.db.engine.fetch_all", new=_fetch_all_error),
        patch("app.db.engine.execute", new=AsyncMock(return_value=1)),
    ):
        writer = MemoryWriter(
            user_extractor=_make_extractor([fact]),
            assistant_extractor=_make_assistant_extractor([]),
            embedding_service=_make_embedder([0.1]),
            contradiction_classifier=_classifier,
        )
        n = await writer.write(
            agent_id=uuid4(),
            user_id=uuid4(),
            run_id=None,
            user_messages=["x"],
            assistant_messages=[],
        )
    # Insert still counted as success; post-pass failure was swallowed
    assert n == 1
