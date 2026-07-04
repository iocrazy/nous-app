"""Phase 2 Task 8 (DONE-GATE) — Step 2: shadow-mode drill.

``FEATURE_DIRECT_CONVERSATIONS='shadow'`` against the real local DB, with a
REAL ``RoutedAiStore`` wrapping a REAL ``LegacyAiStore`` + REAL
``ConversationsAiStore`` (no store mocking). Only the LLM boundary is
mocked for the turn (same seam as test_phase2_flag_on_e2e.py).

Proves the three shadow-mode guarantees from the store_router module
docstring / parity checklist §2 risk #1:

  1. Legacy stays authoritative — every id/value the caller sees is the
     LEGACY row, not the mirror.
  2. The mirror conversation actually lands in the new store (best-effort,
     but real when it succeeds) — queried directly afterward.
  3. A forced mismatch between the legacy row and the mirror emits
     ``[p2-shadow] MISMATCH`` via loguru, without raising or otherwise
     breaking the create/turn flow.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise::

    export INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
    uv run pytest tests/integration/test_phase2_shadow_drill.py -v
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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_USER_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"
_AGENT_SLUG = "script_ai"


class _RunRecorderCM:
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
    """Same LLM-boundary-only patch set as test_phase2_flag_on_e2e.py."""
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
        cache_fingerprint="fp-p2-shadow",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": reply, "tool_calls": []})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 3
    recorder.completion_tokens = 4
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
def shadow_logs(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Capture every record store_router's logger emits — same loguru sink
    pattern as tests/test_store_router.py's ``shadow_logs`` fixture."""
    from app.services.ai.chat import store_router

    captured: List[Dict[str, Any]] = []

    def _sink(message: Any) -> None:
        rec = message.record
        captured.append({"level": rec["level"].name, "message": str(rec["message"])})

    handler_id = store_router.logger.add(_sink, level="INFO")
    yield captured
    store_router.logger.remove(handler_id)


@pytest.fixture
async def shadow_ctx(monkeypatch: pytest.MonkeyPatch):
    """Real-DB fixture: FEATURE_DIRECT_CONVERSATIONS='shadow', a resolved
    personal team + the real ``script_ai`` agent, a raw asyncpg connection,
    and cleanup of both legacy (ai_sessions/ai_messages) rows and any
    mirrored conversations rows this test creates."""
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    from app.core.config import settings
    from app.db import engine as db_engine
    from app.db import session as db_session

    monkeypatch.setattr(settings, "FEATURE_DIRECT_CONVERSATIONS", "shadow")

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        created_team_id: Optional[int] = None
        legacy_session_ids: List[Any] = []
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
                    "__smoke_personal_team_task8_shadow__",
                    _SMOKE_USER_ID,
                    f"SMOKE8SH{int(time.time())}",
                )
                created_team_id = team_id

            yield {
                "conn": conn,
                "user_id": _SMOKE_USER_ID,
                "agent_id": str(agent_row["id"]),
                "agent_slug": _AGENT_SLUG,
                "team_id": team_id,
                "legacy_session_ids": legacy_session_ids,
                "conv_ids": conv_ids,
            }
        finally:
            for sid in legacy_session_ids:
                await conn.execute(
                    "DELETE FROM public.ai_messages WHERE session_id = $1", int(sid)
                )
                await conn.execute(
                    "DELETE FROM public.ai_sessions WHERE id = $1", int(sid)
                )
            for cid in conv_ids:
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
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
    from app.services.ai.chat.legacy_ai_store import LegacyAiStore
    from app.services.ai.chat.store_router import RoutedAiStore

    return AILibraryChatService(
        store=RoutedAiStore(legacy=LegacyAiStore(), new=ConversationsAiStore())
    )


async def test_shadow_legacy_authoritative_and_mirror_lands_in_new_store(
    shadow_ctx: dict,
) -> None:
    """shadow mode: create_session returns the LEGACY row (ids match a real
    ai_sessions row); a mirror conversation row ALSO exists afterward in
    the new store; a subsequent chat turn keeps writing to legacy (ai_
    messages), proving per-session ops never touch the new store's mirror
    in shadow mode."""
    conn: asyncpg.Connection = shadow_ctx["conn"]
    user_id = shadow_ctx["user_id"]
    agent_id = shadow_ctx["agent_id"]
    agent_slug = shadow_ctx["agent_slug"]
    team_id = shadow_ctx["team_id"]

    svc = _build_service()
    title = "__p2_task8_shadow_drill__"

    created = await svc.create_session(
        user_id=UUID(user_id),
        agent_slug=agent_slug,
        title=title,
        team_id=team_id,
    )
    shadow_ctx["legacy_session_ids"].append(created["id"])

    # 1) Legacy is authoritative for the returned result.
    assert created["store_kind"] == "legacy"
    legacy_row = await conn.fetchrow(
        "SELECT id, user_id, title, status FROM public.ai_sessions WHERE id = $1",
        int(created["id"]),
    )
    assert legacy_row is not None
    assert str(legacy_row["user_id"]) == user_id
    assert legacy_row["title"] == title
    assert legacy_row["status"] == "active"

    # 2) Mirror conversation EXISTS in the new store afterward (best-effort
    # write, but real when it lands — this is the observability mirror,
    # never looked up by session_id, so we find it by its distinguishing
    # title + scope instead).
    mirror_row = await conn.fetchrow(
        """
        SELECT c.id FROM public.conversations c
        JOIN public.conversation_ai_meta m ON m.conversation_id = c.id
        WHERE c.title = $1 AND c.scope_id = $2 AND m.agent_slug = $3
        ORDER BY c.created_at DESC LIMIT 1
        """,
        title,
        team_id,
        agent_slug,
    )
    assert mirror_row is not None, "shadow mirror conversation row must exist"
    shadow_ctx["conv_ids"].append(mirror_row["id"])
    assert mirror_row["id"] != int(created["id"])  # unrelated id, as documented

    # 3) A chat turn keeps writing to LEGACY (ai_messages), not the mirror.
    captured_kwargs: Dict[str, Any] = {}
    patches = _llm_boundary_patches(
        agent_id=agent_id,
        agent_slug=agent_slug,
        reply="Shadow-mode reply.",
        captured_kwargs=captured_kwargs,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await svc.chat(
            str(created["id"]), user_id=UUID(user_id), content="Hello in shadow mode"
        )

    assert result["assistant_message"]["content"] == "Shadow-mode reply."
    # store_kind='legacy' -> RunRecorder keeps byte-identical session_id path.
    assert captured_kwargs["session_id"] == str(created["id"])
    assert captured_kwargs["conversation_id"] is None

    legacy_msgs = await conn.fetch(
        "SELECT role, content FROM public.ai_messages WHERE session_id = $1"
        " ORDER BY created_at ASC",
        int(created["id"]),
    )
    assert len(legacy_msgs) == 2
    assert legacy_msgs[0]["role"] == "user"
    assert legacy_msgs[1]["role"] == "assistant"
    assert legacy_msgs[1]["content"] == "Shadow-mode reply."

    # The mirror conversation must NOT have received these turn messages —
    # per-session ops in shadow mode never touch the new store.
    mirror_msg_count = await conn.fetchval(
        "SELECT count(*) FROM public.messages WHERE conversation_id = $1",
        mirror_row["id"],
    )
    assert mirror_msg_count == 0


async def test_shadow_forced_mismatch_logs_without_breaking_the_flow(
    shadow_ctx: dict, shadow_logs: List[Dict[str, Any]]
) -> None:
    """A forced mismatch between the legacy row and the (real) mirror row
    must emit `[p2-shadow] MISMATCH` and must NOT raise or otherwise change
    what the caller sees — create_session still returns the legacy row,
    the real mirror row still lands in the DB with the REAL title (only
    the value ``_shadow_diff`` compares against is mutated, not the write
    itself)."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
    from app.services.ai.chat.legacy_ai_store import LegacyAiStore
    from app.services.ai.chat.store_router import RoutedAiStore

    user_id = shadow_ctx["user_id"]
    agent_slug = shadow_ctx["agent_slug"]
    team_id = shadow_ctx["team_id"]
    conn: asyncpg.Connection = shadow_ctx["conn"]
    title = "__p2_task8_shadow_mismatch__"

    new_store = ConversationsAiStore()
    real_create = new_store.create_session
    real_mirror_id_holder: Dict[str, Any] = {}

    async def _mismatched_create(**kwargs: Any) -> Dict[str, Any]:
        row = await real_create(**kwargs)
        real_mirror_id_holder["id"] = row["id"]
        # Mutate only the value handed back to the diff — the DB row keeps
        # the real title, proving this is a forced *comparison* mismatch,
        # not data corruption.
        return {**row, "title": (row.get("title") or "") + "-MISMATCH"}

    new_store.create_session = _mismatched_create  # type: ignore[method-assign]

    router = RoutedAiStore(legacy=LegacyAiStore(), new=new_store)
    svc_cls = __import__(
        "app.services.ai.chat.ai_library_chat_service",
        fromlist=["AILibraryChatService"],
    ).AILibraryChatService
    svc = svc_cls(store=router)

    created = await svc.create_session(
        user_id=UUID(user_id),
        agent_slug=agent_slug,
        title=title,
        team_id=team_id,
    )
    shadow_ctx["legacy_session_ids"].append(created["id"])
    if "id" in real_mirror_id_holder:
        shadow_ctx["conv_ids"].append(real_mirror_id_holder["id"])

    # The flow did NOT break — legacy result still returned to the caller.
    assert created["store_kind"] == "legacy"
    assert created["title"] == title

    # The real mirror row in the DB keeps the REAL (unmutated) title.
    assert "id" in real_mirror_id_holder
    mirror_db_row = await conn.fetchrow(
        "SELECT title FROM public.conversations WHERE id = $1",
        real_mirror_id_holder["id"],
    )
    assert mirror_db_row is not None
    assert mirror_db_row["title"] == title

    # The forced mismatch was logged.
    mismatches = [r for r in shadow_logs if "MISMATCH" in r["message"]]
    assert len(mismatches) == 1
    assert "title" in mismatches[0]["message"]
    assert f"entity={created['id']}" in mismatches[0]["message"]
