"""RunRecorder must carry issue_id into the agent_runs INSERT so mig-208's
trigger emits + later updates the issue chat row."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4


async def test_insert_row_includes_issue_id():
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
            trigger="issue_dispatch",
            issue_id=409,
        )
        await rec._insert_row()

    assert captured["payload"]["issue_id"] == 409


async def test_insert_row_omits_issue_id_when_not_set():
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

    assert "issue_id" not in captured["payload"]
