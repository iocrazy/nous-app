"""retry_task 在 re-key 的同一条 UPDATE 里累加 metadata.retry_count。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _Session:
    """桩 session：``first()`` 给预置行，``execute`` 记下所有语句。"""

    def __init__(self, row=None):
        self._row = row
        self.values: list[dict] = []

    async def execute(self, stmt, *a, **kw):
        compiled_values = getattr(stmt, "_values", None)
        if compiled_values:
            # Keys come back as Column objects or plain strings depending on
            # how the statement was built; normalise both to the column name.
            self.values.append(
                {
                    str(getattr(k, "name", k)): getattr(v, "value", v)
                    for k, v in compiled_values.items()
                }
            )
        result = MagicMock()
        mappings = MagicMock()
        mappings.first.return_value = self._row
        result.mappings.return_value = mappings
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _row(**over):
    base = {
        "dbos_workflow_id": "parse-old",
        "user_id": "u1",
        "task_type": "parse",
        "status": "failed",
        "metadata": {},
    }
    base.update(over)
    return base


async def _retry(monkeypatch, row, *, new_id="wf-new"):
    from app.services.infra import unified_task_manager as utm

    session = _Session(row)
    monkeypatch.setattr("app.db.session.read_scope", lambda: session)
    monkeypatch.setattr("app.db.session.write_scope", lambda: session)
    mgr = utm.get_task_manager()
    task = await mgr.retry_task("parse-old", "u1", new_workflow_id=new_id)
    return task, session


@pytest.mark.asyncio
async def test_the_first_retry_counts_as_one(monkeypatch):
    task, session = await _retry(monkeypatch, _row())

    written = session.values[-1]
    assert written["metadata"]["retry_count"] == 1
    # 同一条 UPDATE —— 计数和 re-key 不可分开。
    assert written["dbos_workflow_id"] == "wf-new"


@pytest.mark.asyncio
async def test_retries_accumulate_across_attempts(monkeypatch):
    """用户点了四次就是四次。第二次重试不该把计数重置回 1。"""
    task, session = await _retry(monkeypatch, _row(metadata={"retry_count": 3}))

    assert session.values[-1]["metadata"]["retry_count"] == 4


@pytest.mark.asyncio
async def test_a_garbage_counter_does_not_crash_the_retry(monkeypatch):
    """metadata 是 jsonb，谁都能往里写。计数器读不出数字时，重试本身比计数重要。"""
    task, session = await _retry(monkeypatch, _row(metadata={"retry_count": "many"}))

    assert session.values[-1]["metadata"]["retry_count"] == 1


@pytest.mark.asyncio
async def test_other_metadata_survives_the_bump(monkeypatch):
    """下游分支要从 metadata 里读 user_agent / version_id 去重新派发 —— 计数顺手"""
    task, session = await _retry(
        monkeypatch, _row(metadata={"user_agent": "UA/1.0", "version_id": "v9"})
    )

    written = session.values[-1]["metadata"]
    assert written["user_agent"] == "UA/1.0"
    assert written["version_id"] == "v9"
    assert written["retry_count"] == 1


@pytest.mark.asyncio
async def test_the_returned_task_describes_the_attempt_that_just_started(monkeypatch):
    """返回的是重试**之后**的样子。"""
    task, _ = await _retry(monkeypatch, _row(metadata={"retry_count": 1}))

    assert task["metadata"]["retry_count"] == 2
    assert task["dbos_workflow_id"] == "wf-new"


@pytest.mark.asyncio
async def test_a_task_that_is_not_terminal_is_not_retried(monkeypatch):
    """跑着的任务没有「重试」可言 —— 那只会把一个活着的 workflow 从行上摘掉。"""
    task, session = await _retry(monkeypatch, _row(status="processing"))

    assert task is None
    assert session.values == []


@pytest.mark.asyncio
async def test_the_returned_task_matches_every_column_the_update_wrote(monkeypatch):
    """The response used to overlay only metadata / id on the pre-update
    snapshot, so a retried row came back ``status="failed"`` with the old
    error while the database already held it as pending/queued."""
    task, session = await _retry(
        monkeypatch,
        _row(
            phase="failed",
            progress=40,
            error_msg="boom",
            error_code="UNKNOWN",
            started_at="2026-09-24T01:02:03+00:00",
            completed_at="2026-09-24T01:03:03+00:00",
            updated_at="2026-09-24T01:03:03+00:00",
        ),
    )

    written = session.values[-1]
    for column in written:
        if column in ("updated_at", "started_at", "completed_at"):
            continue  # compared below: the response keeps the ISO string
        assert task[column] == written[column], column
    assert task["status"] == "pending"
    assert task["phase"] == "queued"
    assert task["progress"] == 0
    assert task["error_msg"] is None and task["error_code"] is None
    assert task["started_at"] is None and task["completed_at"] is None
    assert isinstance(task["updated_at"], str)
    assert task["updated_at"] == task["metadata"]["last_retry_at"]
