"""RunRecorder must carry conversation_id into the agent_runs INSERT (Phase 1.5:
structural run→conversation link, was metadata-json only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4


async def test_insert_row_includes_conversation_id():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured = {}

    class _Tbl:
        def insert(self, payload):
            captured["payload"] = payload
            return self

        async def execute(self):
            class R:
                data = [{"id": str(uuid4())}]

            return R()

    class _Client:
        def table(self, name):
            return _Tbl()

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=_Client())):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat_summon",
            conversation_id=123456789,
        )
        await rec._insert_row()

    assert captured["payload"]["conversation_id"] == 123456789


async def test_conversation_id_defaults_none():
    from app.services.ai.runner.run_recorder import RunRecorder

    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    assert rec.conversation_id is None


async def test_insert_row_omits_conversation_id_when_not_set():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured = {}

    class _Tbl:
        def insert(self, payload):
            captured["payload"] = payload
            return self

        async def execute(self):
            class R:
                data = [{"id": str(uuid4())}]

            return R()

    class _Client:
        def table(self, name):
            return _Tbl()

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=_Client())):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
        )
        await rec._insert_row()

    assert "conversation_id" not in captured["payload"]
