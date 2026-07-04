"""Phase 2 Task 8 (DONE-GATE) — Step 1: real-DB end-to-end sweep with
``FEATURE_DIRECT_CONVERSATIONS='on'``.

Drives the REAL ``AILibraryChatService`` wired to a REAL ``RoutedAiStore``
(both a real ``LegacyAiStore`` and a real ``ConversationsAiStore`` inside
it — no store mocking anywhere) against the local Supabase Docker
Postgres. The ONLY thing mocked is the LLM boundary: ``build_agent_runner_
stack`` / ``PromptComposer`` / ``AgentRunner`` / ``get_adapter`` /
``SkillToolService`` / ``RunRecorder`` — the exact same seam
``test_task6_run_recorder_store_dispatch.py`` and
``test_issue_agent_executor_p2.py`` use. Everything else — session
creation, message persistence, counter bumps, ownership checks, rename,
list ordering, soft-delete — is the real service running real SQL against
real tables.

With the flag ``on``, ``create_session`` lands directly on
``ConversationsAiStore`` (migration 327/332's ``conversations`` /
``conversation_members`` / ``conversation_ai_meta`` schema), scoped to a
personal team created by this fixture (§3.8/3.9 in the parity checklist —
the personal-DM-scope decision).

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise::

    export INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
    uv run pytest tests/integration/test_phase2_flag_on_e2e.py -v
"""

from __future__ import annotations

import json
import os
import time
from contextlib import ExitStack
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_USER_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"
_AGENT_SLUG = "script_ai"


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _llm_boundary_patches(
    *,
    agent_id: str,
    agent_slug: str,
    reply: str,
    captured_kwargs: Dict[str, Any],
    model: str = "qwen-max",
) -> List[Any]:
    """Build the patch() list for the LLM boundary ONLY — real store, real
    service, real DB. Mirrors test_task6_run_recorder_store_dispatch.py /
    test_issue_agent_executor_p2.py."""
    from app.schemas.ai_library import ComposedSystemPrompt

    composed = ComposedSystemPrompt(
        agent_id=UUID(agent_id),
        agent_slug=agent_slug,
        model=model,
        temperature=0.7,
        max_tokens=4096,
        system_message="You are a helpful test agent.",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp-p2-e2e",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": reply, "tool_calls": []})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 11
    recorder.completion_tokens = 22
    recorder.set_summaries = MagicMock()

    def _fake_run_recorder(**kwargs: Any) -> _RunRecorderCM:
        captured_kwargs.clear()
        captured_kwargs.update(kwargs)
        return _RunRecorderCM(recorder)

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.agent_memory_facts = []
    fake_stack.primary_model = model
    fake_stack.fallback_chain_active = False

    return [
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=_fake_run_recorder,
        ),
    ]


@pytest.fixture
async def flag_on_ctx(monkeypatch: pytest.MonkeyPatch):
    """Real-DB fixture: FEATURE_DIRECT_CONVERSATIONS='on', a resolved
    personal team + the real ``script_ai`` agent for the smoke user, a raw
    asyncpg connection for evidence assertions, and cleanup of every
    conversation this test creates (cascade-deletes members/meta/messages)
    plus any team this fixture had to create."""
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    from app.core.config import settings
    from app.db import engine as db_engine
    from app.db import session as db_session

    monkeypatch.setattr(settings, "FEATURE_DIRECT_CONVERSATIONS", "on")

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        created_team_id: Optional[int] = None
        conv_ids: List[int] = []
        try:
            user_row = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE id = $1", _SMOKE_USER_ID
            )
            if not user_row:
                pytest.skip(
                    f"creator {_SMOKE_USER_ID!r} not found in auth.users — "
                    "cannot run integration smoke"
                )

            agent_row = await conn.fetchrow(
                "SELECT id FROM public.ai_agents WHERE slug = $1", _AGENT_SLUG
            )
            if not agent_row:
                pytest.skip(
                    f"agent slug={_AGENT_SLUG!r} not seeded — cannot run "
                    "integration smoke"
                )

            team_row = await conn.fetchrow(
                "SELECT id FROM public.teams WHERE owner_id = $1 AND kind = 'personal'",
                _SMOKE_USER_ID,
            )
            if team_row:
                team_id = team_row["id"]
            else:
                team_id = await conn.fetchval(
                    """
                    INSERT INTO public.teams (name, owner_id, invite_code, kind)
                    VALUES ($1, $2, $3, 'personal')
                    RETURNING id
                    """,
                    "__smoke_personal_team_task8__",
                    _SMOKE_USER_ID,
                    f"SMOKE8{int(time.time())}",
                )
                created_team_id = team_id

            yield {
                "conn": conn,
                "user_id": _SMOKE_USER_ID,
                "agent_id": str(agent_row["id"]),
                "agent_slug": _AGENT_SLUG,
                "team_id": team_id,
                "conv_ids": conv_ids,
            }
        finally:
            for cid in conv_ids:
                # conversations FK-cascades members/meta/messages/memory.
                await conn.execute(
                    "DELETE FROM public.conversations WHERE id = $1", cid
                )
            if created_team_id is not None:
                await conn.execute(
                    "DELETE FROM public.teams WHERE id = $1", created_team_id
                )
            await conn.close()

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


def _build_service():
    """A REAL AILibraryChatService wired to a REAL RoutedAiStore over REAL
    LegacyAiStore + ConversationsAiStore — no store mocking."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
    from app.services.ai.chat.legacy_ai_store import LegacyAiStore
    from app.services.ai.chat.store_router import RoutedAiStore

    return AILibraryChatService(
        store=RoutedAiStore(legacy=LegacyAiStore(), new=ConversationsAiStore())
    )


async def test_flag_on_full_lifecycle_sweep(flag_on_ctx: dict) -> None:
    """The Step-1 real-DB E2E sweep: create (lands on conversations, scoped
    to the fixture's personal team) -> buffered chat turn (assert
    messages+decoration+counters+RunRecorder conversation_id) ->
    get_session_detail-shaped read (SessionOut/MessageOut construct) ->
    rename -> list ordering floats the just-chatted session -> soft-delete
    (archived, absent from list)."""
    from app.schemas.ai_library_chat import MessageOut, SessionOut, SessionWithMessages

    conn: asyncpg.Connection = flag_on_ctx["conn"]
    user_id = flag_on_ctx["user_id"]
    agent_id = flag_on_ctx["agent_id"]
    agent_slug = flag_on_ctx["agent_slug"]
    team_id = flag_on_ctx["team_id"]
    conv_ids = flag_on_ctx["conv_ids"]

    svc = _build_service()

    # ---- create_session (flag='on' -> lands on ConversationsAiStore) ----
    created = await svc.create_session(
        user_id=UUID(user_id),
        agent_slug=agent_slug,
        title="__p2_task8_flag_on__",
        team_id=team_id,
        context_type="script",
        context_id="task8-flag-on",
    )
    session_id = int(created["id"])
    conv_ids.append(session_id)

    assert created["store_kind"] == "conversations"
    assert created["status"] == "active"
    assert created["team_id"] == team_id

    # Evidence: real conversations / conversation_members / conversation_ai_meta rows.
    conv_row = await conn.fetchrow(
        "SELECT type, scope_id, title, archived_at FROM public.conversations WHERE id = $1",
        session_id,
    )
    assert conv_row is not None
    assert conv_row["type"] == "direct_agent"
    assert conv_row["scope_id"] == team_id
    assert conv_row["archived_at"] is None

    member_rows = await conn.fetch(
        "SELECT member_type, user_id, agent_id FROM public.conversation_members"
        " WHERE conversation_id = $1",
        session_id,
    )
    assert len(member_rows) == 2
    member_types = {r["member_type"] for r in member_rows}
    assert member_types == {"user", "agent"}
    user_member = next(r for r in member_rows if r["member_type"] == "user")
    assert str(user_member["user_id"]) == user_id
    agent_member = next(r for r in member_rows if r["member_type"] == "agent")
    assert str(agent_member["agent_id"]) == agent_id

    meta_row = await conn.fetchrow(
        "SELECT agent_slug, total_tokens, message_count FROM public.conversation_ai_meta"
        " WHERE conversation_id = $1",
        session_id,
    )
    assert meta_row is not None
    assert meta_row["agent_slug"] == agent_slug
    assert meta_row["total_tokens"] == 0
    assert meta_row["message_count"] == 0

    # ---- buffered chat turn (mock ONLY the LLM boundary) ----
    captured_kwargs: Dict[str, Any] = {}
    patches = _llm_boundary_patches(
        agent_id=agent_id,
        agent_slug=agent_slug,
        reply="Hello! I am the test agent.",
        captured_kwargs=captured_kwargs,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await svc.chat(
            str(session_id), user_id=UUID(user_id), content="Hello agent"
        )

    assert result["assistant_message"]["content"] == "Hello! I am the test agent."

    # RunRecorder kwargs carried conversation_id (Task 6 dispatch contract).
    assert captured_kwargs["conversation_id"] == session_id
    assert isinstance(captured_kwargs["conversation_id"], int)
    assert captured_kwargs["session_id"] is None
    assert captured_kwargs["trigger"] == "chat"

    # Evidence: real messages rows, decoration folded into body.meta.
    msg_rows = await conn.fetch(
        "SELECT id, seq, sender_type, from_agent_id, body FROM public.messages"
        " WHERE conversation_id = $1 ORDER BY seq ASC",
        session_id,
    )
    assert len(msg_rows) == 2
    user_row_db = msg_rows[0]
    asst_row_db = msg_rows[1]
    assert user_row_db["sender_type"] == "user"
    user_body = user_row_db["body"]
    if isinstance(user_body, str):
        user_body = json.loads(user_body)
    assert user_body["text"] == "Hello agent"

    assert asst_row_db["sender_type"] == "agent"
    assert str(asst_row_db["from_agent_id"]) == agent_id
    asst_body = asst_row_db["body"]
    if isinstance(asst_body, str):
        asst_body = json.loads(asst_body)
    assert asst_body["text"] == "Hello! I am the test agent."
    meta = asst_body["meta"]
    assert meta["agent_id"] == agent_id
    assert meta["prompt_tokens"] == 11
    assert meta["completion_tokens"] == 22
    assert "run_id" in meta

    # Evidence: conversation_ai_meta counters bumped.
    meta_after = await conn.fetchrow(
        "SELECT total_tokens, message_count FROM public.conversation_ai_meta"
        " WHERE conversation_id = $1",
        session_id,
    )
    assert meta_after["total_tokens"] == 33  # 11 + 22
    assert meta_after["message_count"] == 2

    # ---- get_session_detail-shaped read -> legacy-shaped dicts ----
    session_dict = await svc.get_session(str(session_id), user_id=UUID(user_id))
    messages_dict = await svc.get_messages(str(session_id), user_id=UUID(user_id))
    detail = {**session_dict, "messages": messages_dict}

    detail_model = SessionWithMessages(**detail)
    assert detail_model.id == str(session_id)
    assert str(detail_model.user_id) == user_id
    assert len(detail_model.messages) == 2

    session_model = SessionOut(**session_dict)
    assert session_model.id == str(session_id)
    for m in messages_dict:
        mo = MessageOut(**m)
        assert mo.session_id == str(session_id)
    assert [m.role for m in [MessageOut(**m) for m in messages_dict]] == [
        "user",
        "assistant",
    ]

    # ---- rename ----
    renamed = await svc.update_session(
        str(session_id), user_id=UUID(user_id), title="Renamed via Task 8"
    )
    assert renamed["title"] == "Renamed via Task 8"

    # ---- list ordering: just-chatted-in session floats to top ----
    other_created = await svc.create_session(
        user_id=UUID(user_id),
        agent_slug=agent_slug,
        title="__p2_task8_flag_on_other__",
        team_id=team_id,
    )
    other_id = int(other_created["id"])
    conv_ids.append(other_id)

    listed = await svc.list_sessions(
        user_id=UUID(user_id), agent_slug=agent_slug, limit=50
    )
    ids_in_order = [int(s["id"]) for s in listed]
    assert session_id in ids_in_order
    assert other_id in ids_in_order
    # `other` was created AFTER the rename touched session_id's updated_at,
    # so `other` should currently be first...
    assert ids_in_order.index(other_id) < ids_in_order.index(session_id)

    # ...but chatting in session_id again bumps its updated_at back to now,
    # floating it above `other`.
    captured_kwargs2: Dict[str, Any] = {}
    patches2 = _llm_boundary_patches(
        agent_id=agent_id,
        agent_slug=agent_slug,
        reply="Second turn.",
        captured_kwargs=captured_kwargs2,
    )
    with ExitStack() as stack:
        for p in patches2:
            stack.enter_context(p)
        await svc.chat(str(session_id), user_id=UUID(user_id), content="Again")

    listed_after = await svc.list_sessions(
        user_id=UUID(user_id), agent_slug=agent_slug, limit=50
    )
    ids_after = [int(s["id"]) for s in listed_after]
    assert ids_after.index(session_id) < ids_after.index(other_id)

    # ---- soft-delete: archived, absent from list, 404 on get ----
    await svc.delete_session(str(session_id), user_id=UUID(user_id))

    archived_row = await conn.fetchrow(
        "SELECT archived_at FROM public.conversations WHERE id = $1", session_id
    )
    assert archived_row["archived_at"] is not None

    listed_final = await svc.list_sessions(
        user_id=UUID(user_id), agent_slug=agent_slug, limit=50
    )
    assert session_id not in [int(s["id"]) for s in listed_final]

    with pytest.raises(HTTPException) as exc_info:
        await svc.get_session(str(session_id), user_id=UUID(user_id))
    assert exc_info.value.status_code == 404


async def test_flag_on_streaming_persists_one_final_assistant_row(
    flag_on_ctx: dict,
) -> None:
    """Streaming path (chat_stream) must still: persist user+assistant
    messages (ONE final assistant row, not incremental), bump counters,
    and route RunRecorder via conversation_id — same tail as the buffered
    turn above."""
    from app.services.ai.adapters.base import StreamChunk

    conn: asyncpg.Connection = flag_on_ctx["conn"]
    user_id = flag_on_ctx["user_id"]
    agent_id = flag_on_ctx["agent_id"]
    agent_slug = flag_on_ctx["agent_slug"]
    team_id = flag_on_ctx["team_id"]
    conv_ids = flag_on_ctx["conv_ids"]

    svc = _build_service()

    created = await svc.create_session(
        user_id=UUID(user_id),
        agent_slug=agent_slug,
        title="__p2_task8_flag_on_stream__",
        team_id=team_id,
    )
    session_id = int(created["id"])
    conv_ids.append(session_id)

    from app.schemas.ai_library import ComposedSystemPrompt

    composed = ComposedSystemPrompt(
        agent_id=UUID(agent_id),
        agent_slug=agent_slug,
        model="qwen-max",
        temperature=0.7,
        max_tokens=4096,
        system_message="You are a helpful test agent.",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp-p2-e2e-stream",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    async def _stream_gen(*_a: Any, **_kw: Any):
        yield StreamChunk(delta_text="Hel")
        yield StreamChunk(delta_text="lo!")

    runner = MagicMock()
    runner.stream_turn = _stream_gen

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 5
    recorder.completion_tokens = 6
    recorder.set_summaries = MagicMock()

    captured_kwargs: Dict[str, Any] = {}

    def _fake_run_recorder(**kwargs: Any) -> _RunRecorderCM:
        captured_kwargs.clear()
        captured_kwargs.update(kwargs)
        return _RunRecorderCM(recorder)

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.agent_memory_facts = []
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False

    deltas: List[str] = []

    async def _on_chunk(text: str) -> None:
        deltas.append(text)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=_fake_run_recorder,
        ),
    ):
        result = await svc.chat(
            str(session_id),
            user_id=UUID(user_id),
            content="Stream please",
            chunk_callback=_on_chunk,
        )

    assert "".join(deltas) == "Hello!"
    assert result["assistant_message"]["content"] == "Hello!"
    assert captured_kwargs["conversation_id"] == session_id
    assert captured_kwargs["session_id"] is None

    # Exactly ONE final assistant row persisted (not incrementally per chunk).
    msg_rows = await conn.fetch(
        "SELECT sender_type, body FROM public.messages"
        " WHERE conversation_id = $1 ORDER BY seq ASC",
        session_id,
    )
    assert len(msg_rows) == 2
    assert msg_rows[0]["sender_type"] == "user"
    assert msg_rows[1]["sender_type"] == "agent"
    asst_body = msg_rows[1]["body"]
    if isinstance(asst_body, str):
        asst_body = json.loads(asst_body)
    assert asst_body["text"] == "Hello!"

    meta_after = await conn.fetchrow(
        "SELECT total_tokens, message_count FROM public.conversation_ai_meta"
        " WHERE conversation_id = $1",
        session_id,
    )
    assert meta_after["total_tokens"] == 11  # 5 + 6
    assert meta_after["message_count"] == 2
