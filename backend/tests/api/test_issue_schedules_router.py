"""``GET /api/v1/issues/{id}/schedules`` — what is armed on this issue.

Two row shapes answer to one issue: the one-shot wake-ups that point AT it
(``payload.issue_id``) and the routine that CREATED it
(``payload.last_issue_id`` — the only field an agent_routine payload has that
names an issue). Visibility is the issue's, not the schedule owner's: anyone
who can read the issue can see what will wake it.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

import app.db.session as db_session

mod = import_module("app.api.issues_router")

pytestmark = pytest.mark.unit

_ME = "11111111-1111-4111-8111-111111111111"


class _Auth:
    def __init__(self, user_id: str = _ME):
        self.user_id = user_id


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, result):
        self.statements: list = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._result


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _patch_issue(monkeypatch, *, visible: bool = True, row: Any = None):
    class _Repo:
        async def get_by_id(self, issue_id):
            return row if row is not None else {"id": issue_id}

    async def _visible(row_, user_id):
        return visible

    monkeypatch.setattr(mod, "issue_repository", _Repo())
    monkeypatch.setattr(mod, "is_issue_visible", _visible)


def _wakeup_row(**over):
    row = {
        "id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "task_type": "issue_wakeup",
        "cron_expr": None,
        "next_fire_at": datetime.datetime(2026, 9, 11, 9, tzinfo=datetime.timezone.utc),
        "payload": {"issue_id": 123, "text": "check the render", "created_by": "agent"},
        "enabled": True,
        "pause_reason": None,
    }
    row.update(over)
    return row


def _routine_row(**over):
    row = {
        "id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "task_type": "agent_routine",
        "cron_expr": "0 9 * * *",
        "next_fire_at": datetime.datetime(2026, 9, 12, 9, tzinfo=datetime.timezone.utc),
        "payload": {"last_issue_id": 123, "prompt_md": "Summarize yesterday"},
        "enabled": True,
        "pause_reason": None,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_an_invisible_issue_is_404_and_reads_nothing(monkeypatch):
    _patch_issue(monkeypatch, visible=False)

    def _explode():
        raise AssertionError("no schedule read for an issue the caller cannot see")

    monkeypatch.setattr(db_session, "read_scope", _explode)

    with pytest.raises(HTTPException) as exc:
        await mod.list_issue_schedules(123, _Auth())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_missing_issue_is_404(monkeypatch):
    class _Repo:
        async def get_by_id(self, issue_id):
            return None

    monkeypatch.setattr(mod, "issue_repository", _Repo())
    with pytest.raises(HTTPException) as exc:
        await mod.list_issue_schedules(123, _Auth())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_both_row_shapes_come_back_in_one_list(monkeypatch):
    _patch_issue(monkeypatch)
    session = _FakeSession(_Result([_wakeup_row(), _routine_row()]))
    monkeypatch.setattr(db_session, "read_scope", _cm(session))

    body = await mod.list_issue_schedules(123, _Auth())

    assert list(body) == ["items"]
    wake, routine = body["items"]
    assert set(wake) == {
        "id",
        "task_type",
        "fire_at",
        "cron_expr",
        "text",
        "created_by",
        "enabled",
        # Why a disabled row is disabled — fired_once / issue_terminal /
        # stale / dispatch_failed all look identical without it.
        "pause_reason",
    }
    assert wake["pause_reason"] is None
    assert wake["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert wake["task_type"] == "issue_wakeup"
    assert wake["cron_expr"] is None
    assert wake["fire_at"] == "2026-09-11T09:00:00+00:00"
    assert wake["text"] == "check the render"
    assert wake["created_by"] == "agent"
    assert wake["enabled"] is True

    # A routine has no `text`; its prompt is what it will say.
    assert routine["text"] == "Summarize yesterday"
    assert routine["created_by"] == "user"
    assert routine["cron_expr"] == "0 9 * * *"


@pytest.mark.asyncio
async def test_the_query_matches_each_shape_on_its_own_field(monkeypatch):
    _patch_issue(monkeypatch)
    session = _FakeSession(_Result([]))
    monkeypatch.setattr(db_session, "read_scope", _cm(session))

    await mod.list_issue_schedules(123, _Auth())

    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql, binds = str(compiled), dict(compiled.params)
    assert "user_schedules" in sql
    # live rows first, then by fire time
    assert (
        "ORDER BY public.user_schedules.enabled DESC, "
        "public.user_schedules.next_fire_at" in sql
    )
    # Both discriminators are present, and the issue id is bound as text (the
    # payload is jsonb; ->> yields text, so a bigint bind would not match).
    assert "issue_wakeup" in binds.values()
    assert "agent_routine" in binds.values()
    assert "123" in binds.values()
    # Burned rows must not pile up on a long-lived issue for ever: only live
    # rows, or recently stopped ones, and never more than a page of them.
    assert "enabled" in sql
    assert "paused_at" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_a_burned_row_still_reports_why_it_stopped(monkeypatch):
    _patch_issue(monkeypatch)
    session = _FakeSession(
        _Result([_wakeup_row(enabled=False, pause_reason="dispatch_failed")])
    )
    monkeypatch.setattr(db_session, "read_scope", _cm(session))
    item = (await mod.list_issue_schedules(123, _Auth()))["items"][0]
    assert item["enabled"] is False
    assert item["pause_reason"] == "dispatch_failed"


@pytest.mark.asyncio
async def test_a_row_with_no_fire_time_reports_null_rather_than_crashing(monkeypatch):
    _patch_issue(monkeypatch)
    session = _FakeSession(_Result([_wakeup_row(next_fire_at=None, payload={})]))
    monkeypatch.setattr(db_session, "read_scope", _cm(session))

    item = (await mod.list_issue_schedules(123, _Auth()))["items"][0]
    assert item["fire_at"] is None
    assert item["text"] == ""
    assert item["created_by"] == "user"
