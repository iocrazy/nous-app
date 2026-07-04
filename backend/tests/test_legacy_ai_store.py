"""Unit tests for LegacyAiStore — the MessageStore extraction seam.

One test per op (S1-S6, M1-M3), asserting the exact table/filter/payload
shape pulled from the parity checklist (docs/superpowers/specs/
2026-07-03-phase2-parity-checklist.md §1a/1b). These lock in "zero
behavior change" for the extraction: if LegacyAiStore's SQL shape ever
drifts from what AILibraryChatService used to do inline, one of these
should fail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai.chat.legacy_ai_store import LegacyAiStore


def _make_client_and_store():
    """A MagicMock supabase client + a LegacyAiStore wired to return it."""
    client = MagicMock()
    store = LegacyAiStore(get_client=AsyncMock(return_value=client))
    return client, store


@pytest.mark.asyncio
async def test_create_session_inserts_defaults_and_omits_optional_cols() -> None:
    """S1: insert writes status='active', total_tokens=0, message_count=0
    and omits project_id/team_id/context_type/context_id when None."""
    client, store = _make_client_and_store()
    captured: dict = {}

    table = MagicMock()

    def _insert(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[{**payload, "id": "1"}]))
        return chain

    table.insert = _insert
    client.table.return_value = table

    user_id = str(uuid4())
    row = await store.create_session(
        user_id=user_id,
        agent_slug="script_ai",
        agent_id="agent-1",
        title="New chat",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    client.table.assert_called_with("ai_sessions")
    assert captured == {
        "user_id": user_id,
        "agent_id": "agent-1",
        "agent_slug": "script_ai",
        "title": "New chat",
        "status": "active",
        "total_tokens": 0,
        "message_count": 0,
    }
    assert "project_id" not in captured
    assert "team_id" not in captured
    assert "context_type" not in captured
    assert "context_id" not in captured
    assert row["id"] == "1"


@pytest.mark.asyncio
async def test_create_session_includes_optional_cols_when_provided() -> None:
    """S1: optional columns are included when not None."""
    client, store = _make_client_and_store()
    captured: dict = {}

    table = MagicMock()

    def _insert(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[payload]))
        return chain

    table.insert = _insert
    client.table.return_value = table

    await store.create_session(
        user_id=str(uuid4()),
        agent_slug="script_ai",
        agent_id="agent-1",
        title="New chat",
        project_id=42,
        team_id=7,
        context_type="issue",
        context_id="issue-9",
    )

    assert captured["project_id"] == 42
    assert captured["team_id"] == 7
    assert captured["context_type"] == "issue"
    assert captured["context_id"] == "issue-9"


@pytest.mark.asyncio
async def test_list_sessions_filters_and_orders() -> None:
    """S2: select("*").eq(user_id).neq(status,'deleted').order(updated_at desc).limit(n)
    plus optional agent_slug / project_id eq-filters."""
    client, store = _make_client_and_store()
    calls: list[tuple[str, tuple, dict]] = []

    q = MagicMock()

    def _record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return q

        return _fn

    q.select = _record("select")
    q.eq = _record("eq")
    q.neq = _record("neq")
    q.order = _record("order")
    q.limit = _record("limit")
    q.execute = AsyncMock(return_value=MagicMock(data=[{"id": "1"}]))
    client.table.return_value = q

    user_id = str(uuid4())
    rows = await store.list_sessions(
        user_id=user_id, agent_slug="script_ai", project_id=5, limit=50
    )

    client.table.assert_called_with("ai_sessions")
    names = [c[0] for c in calls]
    assert names[0] == "select"
    assert calls[0][1] == ("*",)
    # eq(user_id) is first eq-call; neq(status, 'deleted'); order desc; limit;
    # then optional eq(agent_slug), eq(project_id).
    eq_calls = [c for c in calls if c[0] == "eq"]
    assert eq_calls[0][1] == ("user_id", user_id)
    assert ("neq", ("status", "deleted"), {}) in calls
    assert ("order", ("updated_at",), {"desc": True}) in calls
    assert ("limit", (50,), {}) in calls
    assert ("eq", ("agent_slug", "script_ai"), {}) in calls
    assert ("eq", ("project_id", 5), {}) in calls
    assert rows == [{"id": "1"}]


@pytest.mark.asyncio
async def test_list_sessions_omits_optional_filters_when_none() -> None:
    """S2: agent_slug / project_id eq-filters are not applied when None."""
    client, store = _make_client_and_store()
    calls: list[tuple] = []

    q = MagicMock()

    def _record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return q

        return _fn

    q.select = _record("select")
    q.eq = _record("eq")
    q.neq = _record("neq")
    q.order = _record("order")
    q.limit = _record("limit")
    q.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table.return_value = q

    await store.list_sessions(
        user_id=str(uuid4()), agent_slug=None, project_id=None, limit=50
    )

    eq_calls = [c for c in calls if c[0] == "eq"]
    # Only the user_id eq — no agent_slug/project_id eq calls.
    assert len(eq_calls) == 1
    assert eq_calls[0][1] == ("user_id", eq_calls[0][1][1])


@pytest.mark.asyncio
async def test_get_session_uses_maybe_single_no_ownership_check() -> None:
    """S3: select("*").eq(id).maybe_single() — no ownership filtering here
    (that lives in the service)."""
    client, store = _make_client_and_store()
    row = {"id": "1", "user_id": "someone-else"}

    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.maybe_single.return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data=row))
    client.table.return_value = q

    got = await store.get_session(session_id=1)

    client.table.assert_called_with("ai_sessions")
    q.eq.assert_called_with("id", "1")
    q.maybe_single.assert_called_once()
    # No .neq / user_id filter anywhere — ownership is not enforced here.
    assert not hasattr(q, "neq") or not q.neq.called
    # Task 6: store_kind is stamped onto the returned dict (dispatch marker
    # for RunRecorder's session_id vs conversation_id choice) — not a real
    # ai_sessions column, so it's added on top of the raw row.
    assert got == {**row, "store_kind": "legacy"}


@pytest.mark.asyncio
async def test_get_session_returns_none_when_missing() -> None:
    """S3: missing row → None (no exception raised by the store)."""
    client, store = _make_client_and_store()

    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.maybe_single.return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data=None))
    client.table.return_value = q

    got = await store.get_session(session_id=1)
    assert got is None


@pytest.mark.asyncio
async def test_rename_session_updates_title_only() -> None:
    """S4: update({title}).eq(id) — no other columns touched."""
    client, store = _make_client_and_store()
    captured: dict = {}

    q = MagicMock()

    def _update(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute = AsyncMock(return_value=MagicMock(data=[{"id": "1", **payload}]))
        return chain

    q.update = _update
    client.table.return_value = q

    row = await store.rename_session(session_id=1, title="Renamed")

    client.table.assert_called_with("ai_sessions")
    assert captured == {"title": "Renamed"}
    assert row["title"] == "Renamed"


@pytest.mark.asyncio
async def test_soft_delete_session_writes_status_deleted() -> None:
    """S5: update({'status': 'deleted'}).eq(id)."""
    client, store = _make_client_and_store()
    captured: dict = {}

    q = MagicMock()

    def _update(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute = AsyncMock(return_value=MagicMock(data=[]))
        return chain

    q.update = _update
    client.table.return_value = q

    await store.soft_delete_session(session_id=1)

    client.table.assert_called_with("ai_sessions")
    assert captured == {"status": "deleted"}


@pytest.mark.asyncio
async def test_bump_counters_writes_caller_provided_absolute_values() -> None:
    """S6: the store writes exactly the values the caller passes — no
    read-modify-write, no re-fetch. The SERVICE is responsible for
    computing prior + turn off its in-memory session snapshot (as it does
    today) before calling bump_counters."""
    client, store = _make_client_and_store()
    captured: dict = {}

    q = MagicMock()

    def _update(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute = AsyncMock(return_value=MagicMock(data=[]))
        return chain

    q.update = _update
    client.table.return_value = q

    await store.bump_counters(session_id=1, add_tokens=149, add_messages=4)

    client.table.assert_called_with("ai_sessions")
    assert captured == {"total_tokens": 149, "message_count": 4}
    # No read performed by the store itself.
    client.table.return_value.select.assert_not_called()


@pytest.mark.asyncio
async def test_get_messages_orders_asc_and_limits() -> None:
    """M1: select("*").eq(session_id).order(created_at asc).limit(200)."""
    client, store = _make_client_and_store()
    calls: list[tuple] = []

    q = MagicMock()

    def _record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return q

        return _fn

    q.select = _record("select")
    q.eq = _record("eq")
    q.order = _record("order")
    q.limit = _record("limit")
    q.execute = AsyncMock(return_value=MagicMock(data=[{"id": "1"}]))
    client.table.return_value = q

    rows = await store.get_messages(session_id=1, limit=200)

    client.table.assert_called_with("ai_messages")
    assert ("eq", ("session_id", "1"), {}) in calls
    assert ("order", ("created_at",), {"desc": False}) in calls
    assert ("limit", (200,), {}) in calls
    assert rows == [{"id": "1"}]


@pytest.mark.asyncio
async def test_append_user_message_writes_only_session_role_content() -> None:
    """M2: insert writes ONLY session_id/role/content — no tokens, no
    agent_id, no metadata, even though user_id is accepted."""
    client, store = _make_client_and_store()
    captured: dict = {}

    q = MagicMock()

    def _insert(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[{**payload, "id": "1"}]))
        return chain

    q.insert = _insert
    client.table.return_value = q

    row = await store.append_user_message(
        session_id=1, user_id=str(uuid4()), content="Hello"
    )

    client.table.assert_called_with("ai_messages")
    assert captured == {"session_id": "1", "role": "user", "content": "Hello"}
    assert row["id"] == "1"


@pytest.mark.asyncio
async def test_append_assistant_message_includes_usage_and_metadata() -> None:
    """M3: insert includes agent_id/prompt_tokens/completion_tokens/metadata_json."""
    client, store = _make_client_and_store()
    captured: dict = {}
    agent_id = str(uuid4())

    q = MagicMock()

    def _insert(payload):
        captured.update(payload)
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[{**payload, "id": "1"}]))
        return chain

    q.insert = _insert
    client.table.return_value = q

    metadata = {"run_id": "run-1", "tool_calls": [{"name": "search"}]}
    row = await store.append_assistant_message(
        session_id=1,
        agent_id=agent_id,
        content="Hi there",
        prompt_tokens=42,
        completion_tokens=7,
        metadata=metadata,
    )

    client.table.assert_called_with("ai_messages")
    assert captured == {
        "session_id": "1",
        "role": "assistant",
        "content": "Hi there",
        "agent_id": agent_id,
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "metadata_json": metadata,
    }
    assert row["id"] == "1"


def test_store_kind_is_legacy() -> None:
    """Task 6 dispatches on store_kind — must be 'legacy' for this store."""
    assert LegacyAiStore.store_kind == "legacy"


@pytest.mark.asyncio
async def test_create_session_return_dict_carries_store_kind_key() -> None:
    """Task 6: create_session's returned row must carry a 'store_kind' key
    (not just the class attribute) so the service can dispatch RunRecorder's
    session_id vs conversation_id off the session row alone."""
    client, store = _make_client_and_store()

    table = MagicMock()

    def _insert(payload):
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=[{**payload, "id": "1"}]))
        return chain

    table.insert = _insert
    client.table.return_value = table

    row = await store.create_session(
        user_id=str(uuid4()),
        agent_slug="script_ai",
        agent_id="agent-1",
        title="New chat",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    assert row["store_kind"] == "legacy"


@pytest.mark.asyncio
async def test_get_session_return_dict_carries_store_kind_key() -> None:
    """Task 6: get_session's returned row must carry a 'store_kind' key."""
    client, store = _make_client_and_store()

    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.maybe_single.return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data={"id": "1", "user_id": "u1"}))
    client.table.return_value = q

    row = await store.get_session(session_id=1)

    assert row["store_kind"] == "legacy"


@pytest.mark.asyncio
async def test_default_client_resolves_via_service_module_when_none_injected() -> None:
    """No explicit get_client → resolves lazily through the
    ai_library_chat_service module's current get_async_supabase_admin
    attribute (so tests that patch it there, even after constructing the
    service, still intercept every store call). See module docstring."""
    from unittest.mock import AsyncMock

    from app.services.ai.chat import ai_library_chat_service as svc_mod

    fake_client = object()
    store = LegacyAiStore()

    original = svc_mod.get_async_supabase_admin
    svc_mod.get_async_supabase_admin = AsyncMock(return_value=fake_client)
    try:
        got = await store._client()
    finally:
        svc_mod.get_async_supabase_admin = original

    assert got is fake_client
