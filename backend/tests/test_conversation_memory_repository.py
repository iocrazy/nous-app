"""Unit + integration tests for ConversationMemoryRepository (Phase 1.5, Task 3).

Unit tests (default, no DB): capture SQL/params via a small fake-engine
fixture that patches the module-level ``app.db.engine.fetch_one`` /
``app.db.engine.execute`` helpers — the same transport
``conversation_repository`` uses for single-statement reads/writes (see
``get_conversation`` / ``mark_read``).

Integration test (skippable): requires ``INTEGRATION_DATABASE_URL`` env var,
mirroring the ``conv_for_smoke`` pattern in ``test_conversation_repository.py``.
Proves upsert-then-upsert (INSERT .. ON CONFLICT DO UPDATE) round-trips
against real Postgres, and that ``messages_in_range`` returns the expected
ascending, non-deleted slice.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

# ── Fake engine fixture (captures the last fetch_one/execute call) ────────────


class _Captured:
    """Records the SQL + params of the most recent db_engine call."""

    def __init__(self) -> None:
        self._sql: str | None = None
        self._params: dict[str, Any] = {}

    def record(self, sql: str, params: dict[str, Any] | None) -> None:
        self._sql = sql
        self._params = params or {}

    def last_sql(self) -> str:
        assert self._sql is not None, "no db_engine call was captured"
        return self._sql

    def last_params(self) -> dict[str, Any]:
        return self._params


@pytest.fixture
def fake_engine():
    """Patch app.db.engine.fetch_one/execute to capture SQL instead of hitting a DB."""
    captured = _Captured()

    async def fake_fetch_one(sql: str, params: dict | None = None) -> None:
        captured.record(sql, params)
        return None

    async def fake_execute(sql: str, params: dict | None = None) -> int:
        captured.record(sql, params)
        return 1

    with (
        patch("app.db.engine.fetch_one", fake_fetch_one),
        patch("app.db.engine.execute", fake_execute),
    ):
        yield captured


# ── upsert ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict(fake_engine) -> None:
    from app.repositories.conversation_memory_repository import (
        ConversationMemoryRepository,
    )

    repo = ConversationMemoryRepository()
    await repo.upsert(
        conversation_id=1, summary_md="S", last_seq_summarized=40, model="qwen-turbo"
    )
    sql = fake_engine.last_sql()
    assert "INSERT INTO public.conversation_memory" in sql
    assert "ON CONFLICT (conversation_id) DO UPDATE" in sql
    assert fake_engine.last_params()["last_seq"] == 40


@pytest.mark.asyncio
async def test_upsert_coerces_conversation_id_and_seq_to_int(fake_engine) -> None:
    from app.repositories.conversation_memory_repository import (
        ConversationMemoryRepository,
    )

    repo = ConversationMemoryRepository()
    await repo.upsert(
        conversation_id="1", summary_md="S", last_seq_summarized="40", model=None
    )
    params = fake_engine.last_params()
    assert params["cid"] == 1 and isinstance(params["cid"], int)
    assert params["last_seq"] == 40 and isinstance(params["last_seq"], int)
    assert params["model"] is None


# ── load ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_selects_by_conversation(fake_engine) -> None:
    from app.repositories.conversation_memory_repository import (
        ConversationMemoryRepository,
    )

    repo = ConversationMemoryRepository()
    await repo.load(7)
    sql = fake_engine.last_sql()
    assert "FROM public.conversation_memory" in sql
    assert "conversation_id = :cid" in sql
    assert fake_engine.last_params()["cid"] == 7


# ── singleton ───────────────────────────────────────────────────────────────


def test_get_conversation_memory_repository_returns_singleton() -> None:
    import app.repositories.conversation_memory_repository as mod
    from app.repositories.conversation_memory_repository import (
        ConversationMemoryRepository,
        get_conversation_memory_repository,
    )

    mod._repo = None
    r1 = get_conversation_memory_repository()
    r2 = get_conversation_memory_repository()
    assert r1 is r2
    assert isinstance(r1, ConversationMemoryRepository)
    mod._repo = None


# ── Integration test (skippable without INTEGRATION_DATABASE_URL) ─────────────

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_SCOPE_ID = 285274231427073
_SMOKE_CREATOR_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"


@pytest.fixture
async def conv_for_memory_smoke():
    """Create a real conversation (+3 sent messages) against the integration DB.

    Mirrors ``conv_for_smoke`` in test_conversation_repository.py: patches
    db_engine at the integration DSN, creates a throwaway conversation, and
    cleans up (conversation_memory rows cascade-delete via the conversation FK).
    """
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    import asyncpg

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        conv_id = None
        try:
            row = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE id = $1",
                _SMOKE_CREATOR_ID,
            )
            if not row:
                pytest.skip(
                    f"creator {_SMOKE_CREATOR_ID!r} not found in auth.users — "
                    "cannot run integration smoke"
                )

            import app.repositories.conversation_memory_repository as _mem_mod
            import app.repositories.conversation_repository as _conv_mod
            from app.repositories.conversation_memory_repository import (
                get_conversation_memory_repository,
            )
            from app.repositories.conversation_repository import (
                get_conversation_repository,
            )

            _conv_mod._repo = None
            _mem_mod._repo = None
            conv_repo = get_conversation_repository()

            c = await conv_repo.create_conversation(
                creator_id=_SMOKE_CREATOR_ID,
                scope_id=_SMOKE_SCOPE_ID,
                type="group",
                name="__smoke_conv_memory_repo_test__",
                history_mode="shared",
                member_ids=[],
            )
            conv_id = c["id"]

            for i in range(3):
                await conv_repo.send_message(
                    conversation_id=conv_id,
                    sender_id=_SMOKE_CREATOR_ID,
                    sender_type="user",
                    type="text",
                    body={"text": f"memory smoke message {i + 1}"},
                    parent_id=None,
                )

            yield conv_id

        finally:
            if conv_id is not None:
                await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)
            await conn.close()
            _conv_mod._repo = None  # type: ignore[union-attr]
            _mem_mod._repo = None  # type: ignore[union-attr]

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_smoke_upsert_twice_load_returns_updated_row(
    conv_for_memory_smoke: int,
) -> None:
    """Real-DB smoke: upsert twice (2nd updates), load returns the latest row;
    messages_in_range over 3 sent messages returns seq 1,2,3 ascending."""
    import app.repositories.conversation_memory_repository as _mem_mod
    import app.repositories.conversation_repository as _conv_mod

    mem_repo = _mem_mod.get_conversation_memory_repository()
    conv_repo = _conv_mod.get_conversation_repository()
    conv_id = conv_for_memory_smoke

    await mem_repo.upsert(
        conversation_id=conv_id,
        summary_md="first summary",
        last_seq_summarized=1,
        model="qwen-turbo",
    )
    await mem_repo.upsert(
        conversation_id=conv_id,
        summary_md="second summary",
        last_seq_summarized=2,
        model="qwen-turbo",
    )

    loaded = await mem_repo.load(conv_id)
    assert loaded is not None
    assert loaded["summary_md"] == "second summary"
    assert loaded["last_seq_summarized"] == 2
    assert loaded["model"] == "qwen-turbo"

    rows = await conv_repo.messages_in_range(
        conversation_id=conv_id, from_seq=1, to_seq=3
    )
    assert [r["seq"] for r in rows] == [1, 2, 3]
