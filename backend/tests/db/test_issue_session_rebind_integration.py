"""Reassigning an issue makes the NEXT turn run the new agent (FH2 T5), on a
real Postgres.

The unit tests stub the session, so only this file proves that the rebind
UPDATE lands on ``conversation_ai_meta`` and that the chain the product relies
on holds end to end: rebind → the store's session read → ``run_session_turn``
resolving the agent by that slug → the same agent ``dispatch_preview`` names.
Before the fix the preview said B while A ran. Same harness as
``tests/db/test_conversations_store_question_integration.py``; skips cleanly
without ``INTEGRATION_DATABASE_URL``.
"""

from __future__ import annotations

import importlib
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — issue session rebind needs a DB.",
)


@pytest.fixture
async def orm_dsn():
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    import asyncpg

    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def reassigned(orm_dsn, pg):
    """An issue whose session was created for agent A, then reassigned to B."""
    tag = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) RETURNING id",
        "Rebind Test Team",
        user_id,
        uuid.uuid4().hex[:10],
    )
    agents = {}
    for key in ("a", "b"):
        slug = f"rebind-{key}-{tag}"
        agents[key] = {
            "slug": slug,
            "id": await pg.fetchval(
                "INSERT INTO ai_agents (name, slug) VALUES ($1, $2) RETURNING id",
                f"Rebind {key.upper()}",
                slug,
            ),
        }
    # Through the real store: the joined session read needs the membership
    # rows create_session writes, not just conversations + meta.
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    created = await ConversationsAiStore().create_session(
        user_id=str(user_id),
        agent_slug=agents["a"]["slug"],
        agent_id=str(agents["a"]["id"]),
        title="Rebind fixture",
        project_id=None,
        team_id=team_id,
        context_type="issue",
        context_id=None,
    )
    conv_id = int(created["id"])
    issue_id = await pg.fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, assignee_agent_id,
                               ai_session_id)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, 'Rebind fixture', 'todo', 'medium', 'manual', $2, $3, $4)
           RETURNING id""",
        f"RB-{tag}",
        user_id,
        agents["b"]["id"],
        conv_id,
    )
    try:
        yield {
            "issue_id": issue_id,
            "conv_id": conv_id,
            "user_id": user_id,
            "a": agents["a"],
            "b": agents["b"],
        }
    finally:
        await pg.execute("DELETE FROM issues WHERE id = $1", issue_id)
        await pg.execute("DELETE FROM messages WHERE conversation_id = $1", conv_id)
        await pg.execute(
            "DELETE FROM conversation_members WHERE conversation_id = $1", conv_id
        )
        await pg.execute("DELETE FROM conversations WHERE id = $1", conv_id)
        for a in agents.values():
            await pg.execute("DELETE FROM ai_agents WHERE id = $1", a["id"])
        await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@_skip
async def test_rebind_lands_and_the_next_turn_resolves_the_new_agent(
    orm_dsn, pg, reassigned, monkeypatch
):
    from app.services.ai.chat import ai_library_chat_service as svc_mod
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
    from app.services.infra import dbos_orchestrator
    from app.services.issues.issue_session import get_or_create_issue_session
    from tests.test_parity_gap_coverage import _chat_env

    b = reassigned["b"]
    sid = await get_or_create_issue_session(reassigned["issue_id"])
    assert sid == str(reassigned["conv_id"])  # same session: history kept

    meta = await pg.fetchrow(
        "SELECT agent_id, agent_slug FROM conversation_ai_meta WHERE conversation_id = $1",
        reassigned["conv_id"],
    )
    assert (meta["agent_id"], meta["agent_slug"]) == (b["id"], b["slug"])

    store = ConversationsAiStore()
    session_row = await store.get_session(session_id=int(sid))
    assert session_row["agent_slug"] == b["slug"]

    # The agent the turn actually runs is whatever get_by_slug is asked for.
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=b["id"]
    ):
        await svc_mod.AILibraryChatService(store=store).run_session_turn(
            int(sid),
            user_id=reassigned["user_id"],
            content="go",
            trigger="issue_dispatch",
        )
        spy = svc_mod.get_agent_repository.return_value.get_by_slug
        assert spy.await_args.args[0] == b["slug"]

    # …and it is the agent the confirm dialog promised.
    issues_router = importlib.import_module("app.api.issues_router")

    async def _visible(row, auth):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(issues_router, "_assert_visibility", _visible)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)
    preview = await issues_router.dispatch_preview(reassigned["issue_id"], object())
    assert preview.agent_id == str(b["id"]) == str(meta["agent_id"])


@_skip
async def test_second_call_is_a_pure_read(orm_dsn, pg, reassigned):
    """Once bound, later turns do not rewrite the meta row."""
    from app.services.issues.issue_session import get_or_create_issue_session

    await get_or_create_issue_session(reassigned["issue_id"])
    xmin = await pg.fetchval(
        "SELECT xmin::text FROM conversation_ai_meta WHERE conversation_id = $1",
        reassigned["conv_id"],
    )
    await get_or_create_issue_session(reassigned["issue_id"])
    assert xmin == await pg.fetchval(
        "SELECT xmin::text FROM conversation_ai_meta WHERE conversation_id = $1",
        reassigned["conv_id"],
    )
