"""Phase 2a Task 4: ConversationsAiStore reads the open question off the
latest assistant message and stamps ``answered`` onto it (jsonb, ORM)."""

import contextlib
import json

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.chat.conversations_ai_store import (
    ConversationsAiStore,
    open_question_from_body,
)

pytestmark = pytest.mark.unit

META_Q = {
    "question_id": "q:7:2",
    "kind": "user",
    "prompt": "?",
    "options": [{"label": "A", "description": None}],
    "allow_free_text": False,
    "run_id": "7",
}


def test_open_question_from_body_requires_an_unanswered_awaiting_input():
    assert (
        open_question_from_body({"text": "", "meta": {"awaiting_input": META_Q}})
        == META_Q
    )
    assert open_question_from_body({"text": "", "meta": {}}) is None
    assert (
        open_question_from_body(
            {"meta": {"awaiting_input": {**META_Q, "answered": {"value": "A"}}}}
        )
        is None
    )
    assert (
        open_question_from_body(json.dumps({"meta": {"awaiting_input": META_Q}}))
        == META_Q
    )
    assert open_question_from_body({"meta": {"awaiting_input": "junk"}}) is None


async def test_latest_assistant_open_question_reads_the_newest_agent_message(
    monkeypatch,
):
    from app.db import session as dbs

    seen = {}

    class _Res:
        def first(self):
            return (5001, {"text": "", "meta": {"awaiting_input": META_Q}})

    class _S:
        async def execute(self, stmt, *a, **k):
            seen["sql"] = str(stmt.compile(dialect=postgresql.dialect())).lower()
            return _Res()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    out = await ConversationsAiStore().latest_assistant_open_question(session_id="42")
    assert out == {"message_id": 5001, "question": META_Q}
    sql = seen["sql"]
    assert (
        "sender_type" in sql and "order by" in sql and "desc" in sql and "limit" in sql
    )
    assert "deleted_at is null" in sql


def test_question_answered_stmt_sets_the_answered_key_only():
    stmt = ConversationsAiStore.question_answered_stmt(
        5001, {"value": "A", "at": "T", "superseded": False}
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "UPDATE public.messages" in sql
    # level-by-level merge, never jsonb_set (which no-ops on a missing path)
    assert "jsonb_set" not in sql and sql.count("||") == 3
    assert "jsonb_build_object" in sql and "coalesce" in sql
    params = compiled.params
    assert {"meta", "awaiting_input", "answered"} <= set(map(str, params.values()))
    assert {"value": "A", "at": "T", "superseded": False} in params.values()
    # The missing-path fallback must be a real empty OBJECT. ``cast("{}", JSONB)``
    # binds the Python str "{}" -> JSON string "{}" -> Postgres makes
    # ``scalar || object`` an ARRAY (schema-drift caught it on a real DB).
    assert "{}" not in params.values()
    assert "jsonb_build_object()" in sql
