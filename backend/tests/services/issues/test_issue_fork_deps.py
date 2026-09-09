"""``issue_fork.default_deps()`` — the real bindings. Statements are captured
off a stubbed ``write_scope`` / ``read_scope`` and compiled, so the SQL each
binding emits is pinned (not the ORM call shape)."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.services.issues import issue_fork as f

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, rows=None):
        self.stmts = []
        self._rows = rows or []

    async def execute(self, stmt, *a, **k):
        self.stmts.append(stmt)

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return self._rows

            @property
            def rowcount(self_inner):
                return 1

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


def _sql(stmt) -> str:
    if isinstance(stmt, str):
        return stmt
    if hasattr(stmt, "compile") and not hasattr(stmt, "_bindparams"):
        return str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    return str(stmt)


async def test_switch_repoints_clears_markers_and_stamps_forked_from_as_service_role():
    sess = _Session()
    with patch("app.db.session.write_scope", _scope(sess)):
        await f.default_deps().switch_session_and_mark(
            9, "200", {"run_id": 42, "at_seq": 4, "steer": False, "steer_text": None}
        )
    assert len(sess.stmts) == 2
    assert "SET LOCAL ROLE service_role" in _sql(sess.stmts[0])
    sql = _sql(sess.stmts[1])
    assert "UPDATE public.issues" in sql
    assert "ai_session_id=200" in sql.replace(" ", "")
    assert "paused_at=NULL" in sql.replace(" ", "")
    assert "- CAST('awaiting_input'" in sql, "the parked-question marker must go"
    assert "jsonb_build_object('forked_from'" in sql and '"run_id": 42' in sql


async def test_restore_puts_pointer_paused_at_and_stamp_back_in_one_transaction():
    sess = _Session()
    with patch("app.db.session.write_scope", _scope(sess)):
        await f.default_deps().restore_session(9, 100, "2026-09-09T09:00:00+00:00")
    assert len(sess.stmts) == 2 and "SET LOCAL ROLE service_role" in _sql(sess.stmts[0])
    sql = _sql(sess.stmts[1])
    assert "ai_session_id=100" in sql.replace(" ", "")
    assert "paused_at='2026-09-09 09:00:00+00:00'" in sql
    assert "- CAST('forked_from'" in sql, "the stamp must go with the pointer"


async def test_release_parked_binds_to_the_input_gate_recipe():
    rel = AsyncMock()
    with patch("app.agent_framework.input_gate.release_parked_workflow", rel):
        await f.default_deps().release_parked("wf-old")
    rel.assert_awaited_once_with("wf-old")


async def test_list_events_reads_only_the_replayable_types_up_to_at_seq():
    repo = AsyncMock()
    repo.list_transcript_events = AsyncMock(return_value=[])
    with patch(
        "app.repositories.agent_runs_repository.get_agent_runs_repository",
        return_value=repo,
    ):
        await f.default_deps().list_events(42, 4)
    repo.list_transcript_events.assert_awaited_once_with(
        42, upto_seq=4, event_types=list(f.REPLAY_EVENT_TYPES)
    )


async def test_origin_messages_stop_at_the_run_start_and_keep_only_replayable_roles():
    rows = [
        {"role": "user", "content": "a", "created_at": "2026-09-09T09:00:00+00:00"},
        {
            "role": "assistant",
            "content": "b",
            "created_at": "2026-09-09T09:01:00+00:00",
        },
        {"role": "user", "content": "c", "created_at": "2026-09-09T10:00:00+00:00"},
    ]
    store = AsyncMock()
    store.get_messages = AsyncMock(return_value=rows)
    with patch(
        "app.services.ai.chat.conversations_ai_store.ConversationsAiStore",
        return_value=store,
    ):
        out = await f.default_deps().list_origin_messages(
            100, "2026-09-09T10:00:00+00:00"
        )
    assert out == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    store.get_messages.assert_awaited_once_with(
        session_id=100, limit=f.ORIGIN_MESSAGE_LIMIT
    )


async def test_append_dispatches_by_role_with_the_session_owner_and_agent():
    store = AsyncMock()
    store.get_session = AsyncMock(return_value={"user_id": "owner", "agent_id": "ag"})
    with patch(
        "app.services.ai.chat.conversations_ai_store.ConversationsAiStore",
        return_value=store,
    ):
        await f.default_deps().append_messages(
            "200",
            [
                {"role": "system", "content": "[Earlier conversation summary]\nS"},
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "act 1"},
            ],
            {"forked_from": {"run_id": 42, "at_seq": 4}},
        )
    store.append_system_message.assert_awaited_once_with(
        session_id=200,
        content="[Earlier conversation summary]\nS",
        metadata={"forked_from": {"run_id": 42, "at_seq": 4}},
    )
    store.append_user_message.assert_awaited_once_with(
        session_id=200, user_id="owner", content="go"
    )
    store.append_assistant_message.assert_awaited_once_with(
        session_id=200,
        agent_id="ag",
        content="act 1",
        prompt_tokens=0,
        completion_tokens=0,
        metadata={"forked_from": {"run_id": 42, "at_seq": 4}},
    )


async def test_supersede_writes_question_answered_superseded_on_the_origin_run():
    writer = AsyncMock()
    with patch(
        "app.services.ai.runner.run_recorder.event_writer_for_run",
        AsyncMock(return_value=writer),
    ):
        await f.default_deps().supersede_question(42, "q:42:4")
    writer.append.assert_awaited_once_with(
        "question_answered",
        {"question_id": "q:42:4", "value": None, "superseded": True},
        turn=None,
        step=None,
    )


async def test_supersede_failure_is_logged_not_raised():
    with patch(
        "app.services.ai.runner.run_recorder.event_writer_for_run",
        AsyncMock(side_effect=RuntimeError("db")),
    ):
        await f.default_deps().supersede_question(42, "q:42:4")  # no raise


async def test_dispatch_binds_to_the_shared_start_execute_issue():
    start = AsyncMock(return_value="wf-9")
    with patch("app.services.issues.issue_dispatch.start_execute_issue", start):
        assert await f.default_deps().dispatch(9) == "wf-9"
    start.assert_awaited_once_with(9)


async def test_get_issue_by_session_binds_to_the_repository():
    repo = AsyncMock()
    repo.get_by_session = AsyncMock(return_value={"id": 9})
    with patch(
        "app.repositories.issue_repository.get_issue_repository", return_value=repo
    ):
        assert await f.default_deps().get_issue_by_session(100) == {"id": 9}
    repo.get_by_session.assert_awaited_once_with(100)
