"""W2a autopilot hardening — schedules_router unit tests.

Covers timezone validation, tz-aware cron next-fire, and the resume endpoint
(re-enable + reset failure run + drop pause metadata + recompute next_fire_at)
with faked read/write scopes — no real DB.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager
from importlib import import_module
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

# app.api.__init__ rebinds the `schedules_router` attribute to the APIRouter,
# so `import app.api.schedules_router as mod` would grab the router, not the
# module. import_module returns the real module from sys.modules.
mod = import_module("app.api.schedules_router")


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


_ME = "11111111-1111-4111-8111-111111111111"


# ── result / session doubles (boundary-stub style) ──────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, result):
        self.statements = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._result


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _full_row(**over):
    row = {
        "id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "user_id": uuid.UUID(_ME),
        "name": "Daily scout",
        "cron_expr": "0 9 * * *",
        "task_type": "agent_routine",
        "payload": {"agent_slug": "x", "prompt_md": "hi"},
        "lane": "scheduled",
        "enabled": True,
        "last_fired_at": None,
        "next_fire_at": datetime.datetime(
            2026, 7, 20, 13, tzinfo=datetime.timezone.utc
        ),
        "fire_count": 3,
        "fail_count": 5,
        "last_error": None,
        "created_at": datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        "updated_at": datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        "timezone": "America/New_York",
        "consecutive_fails": 0,
        "paused_at": None,
        "pause_reason": None,
        "skipped_count": 2,
        "stale_after_minutes": 60,
    }
    row.update(over)
    return row


# ── timezone / cron validation ──────────────────────────────────────────────


def test_validate_timezone_accepts_iana():
    assert isinstance(mod._validate_timezone("America/New_York"), ZoneInfo)


def test_validate_timezone_rejects_unknown():
    with pytest.raises(HTTPException) as exc:
        mod._validate_timezone("Not/AZone")
    assert exc.value.status_code == 400


def test_validate_cron_is_tz_aware_and_returns_utc():
    next_utc = mod._validate_cron("0 9 * * *", "America/New_York")
    assert next_utc.utcoffset() == datetime.timedelta(0)  # stored UTC
    ny = next_utc.astimezone(ZoneInfo("America/New_York"))
    assert (ny.hour, ny.minute) == (9, 0)  # 9am local, DST-safe


def test_validate_cron_rejects_bad_timezone():
    with pytest.raises(HTTPException) as exc:
        mod._validate_cron("0 9 * * *", "Bad/Zone")
    assert exc.value.status_code == 400


def test_validate_cron_rejects_bad_cron():
    with pytest.raises(HTTPException) as exc:
        mod._validate_cron("nonsense expr", "UTC")
    assert exc.value.status_code == 400


# ── resume endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_clears_pause_and_recomputes(monkeypatch):
    read_session = _FakeSession(
        _Result([{"cron_expr": "0 9 * * *", "timezone": "America/New_York"}])
    )
    resumed_row = _full_row(
        enabled=True, consecutive_fails=0, paused_at=None, pause_reason=None
    )
    write_session = _FakeSession(_Result([resumed_row]))
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    resp = await mod.resume_schedule("sched-1", _Auth(_ME))

    assert resp.enabled is True
    assert resp.consecutive_fails == 0
    assert resp.paused_at is None
    assert resp.pause_reason is None
    # The UPDATE recomputed next_fire_at from cron+tz and cleared pause state.
    update_sql = str(write_session.statements[0]).lower()
    assert "update public.user_schedules" in update_sql
    params = write_session.statements[0].compile().params
    assert params.get("enabled") is True
    assert params.get("consecutive_fails") == 0
    assert params.get("paused_at") is None


@pytest.mark.asyncio
async def test_resume_404_when_missing(monkeypatch):
    read_session = _FakeSession(_Result([]))  # no such row for this owner
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))

    def _explode():
        raise AssertionError("write_scope must not open when the row is missing")

    monkeypatch.setattr(mod, "write_scope", _explode)

    with pytest.raises(HTTPException) as exc:
        await mod.resume_schedule("sched-x", _Auth(_ME))
    assert exc.value.status_code == 404


# ── whitelist single source ─────────────────────────────────────────────────


def test_router_whitelist_is_the_engine_registry():
    """The API must accept exactly what the engine can fire. A whitelist wider
    than the engine turns an accepted schedule into an every-minute silent
    skip; a narrower one rejects a type that works."""
    from app.workflows.scheduled_master import SUPPORTED_TASK_TYPES

    assert mod._ALLOWED_TASK_TYPES == SUPPORTED_TASK_TYPES
    assert "ai_transcription" not in SUPPORTED_TASK_TYPES
    assert {"agent_routine", "issue_wakeup"} <= SUPPORTED_TASK_TYPES


# ── issue_wakeup create ─────────────────────────────────────────────────────


def _wakeup_payload(**over):
    body = {
        "task_type": "issue_wakeup",
        "fire_at": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=2),
        "payload": {"issue_id": 123, "text": "check the render"},
    }
    body.update(over)
    return mod.ScheduleCreatePayload(**body)


def _visible(row=None):
    async def _assert(issue_id, auth):
        return row or {"id": issue_id, "created_by_user_id": _ME}

    return _assert


@pytest.mark.asyncio
async def test_wakeup_without_fire_at_is_a_typed_400(monkeypatch):
    monkeypatch.setattr(mod, "assert_issue_visible", _visible())
    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(_wakeup_payload(fire_at=None), _Auth(_ME))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "fire_at_required"


@pytest.mark.asyncio
async def test_wakeup_beyond_thirty_days_is_a_typed_400(monkeypatch):
    monkeypatch.setattr(mod, "assert_issue_visible", _visible())
    far = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=31)
    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(_wakeup_payload(fire_at=far), _Auth(_ME))
    assert exc.value.detail["code"] == "fire_at_out_of_range"


@pytest.mark.asyncio
async def test_wakeup_in_the_past_is_a_typed_400(monkeypatch):
    monkeypatch.setattr(mod, "assert_issue_visible", _visible())
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(_wakeup_payload(fire_at=past), _Auth(_ME))
    assert exc.value.detail["code"] == "fire_at_out_of_range"


@pytest.mark.asyncio
async def test_wakeup_without_text_is_a_typed_400(monkeypatch):
    monkeypatch.setattr(mod, "assert_issue_visible", _visible())
    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(
            _wakeup_payload(payload={"issue_id": 123, "text": "   "}), _Auth(_ME)
        )
    assert exc.value.detail["code"] == "text_required"


@pytest.mark.asyncio
async def test_wakeup_on_an_invisible_issue_is_404(monkeypatch):
    async def _deny(issue_id, auth):
        raise HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(mod, "assert_issue_visible", _deny)

    def _explode():
        raise AssertionError("write_scope must not open for an unseen issue")

    monkeypatch.setattr(mod, "write_scope", _explode)

    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(_wakeup_payload(), _Auth(_ME))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_wakeup_row_is_cron_less_and_fires_at_the_given_time(monkeypatch):
    monkeypatch.setattr(mod, "assert_issue_visible", _visible())
    fire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    created = _full_row(
        task_type="issue_wakeup",
        cron_expr=None,
        name="check the render",
        next_fire_at=fire_at,
        payload={"issue_id": 123, "text": "check the render", "once": True},
    )
    write_session = _FakeSession(_Result([created]))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    resp = await mod.create_schedule(_wakeup_payload(fire_at=fire_at), _Auth(_ME))

    assert resp.cron_expr is None
    assert resp.next_fire_at == fire_at.isoformat()
    params = write_session.statements[0].compile().params
    assert params["cron_expr"] is None
    assert params["next_fire_at"] == fire_at
    assert params["payload"]["once"] is True
    assert params["payload"]["created_by"] == "user"


@pytest.mark.asyncio
async def test_cron_schedule_without_fire_at_still_creates(monkeypatch):
    """Regression: the recurring path must not start demanding fire_at."""
    created = _full_row()
    write_session = _FakeSession(_Result([created]))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    body = mod.ScheduleCreatePayload(
        name="Daily scout",
        cron_expr="0 9 * * *",
        task_type="agent_routine",
        payload={"agent_slug": "x", "prompt_md": "hi"},
    )
    resp = await mod.create_schedule(body, _Auth(_ME))
    assert resp.cron_expr == "0 9 * * *"


@pytest.mark.asyncio
async def test_recurring_type_without_cron_is_a_typed_400(monkeypatch):
    def _explode():
        raise AssertionError("write_scope must not open without a cron")

    monkeypatch.setattr(mod, "write_scope", _explode)
    body = mod.ScheduleCreatePayload(task_type="ai_summary", payload={})
    with pytest.raises(HTTPException) as exc:
        await mod.create_schedule(body, _Auth(_ME))
    assert exc.value.detail["code"] == "cron_required"


# ── a cron-less row must survive every read/update path ─────────────────────


@pytest.mark.asyncio
async def test_list_does_not_500_on_a_cron_less_row(monkeypatch):
    read_session = _FakeSession(_Result([_full_row(cron_expr=None)]))
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    rows = await mod.list_schedules(_Auth(_ME))
    assert rows[0].cron_expr is None


@pytest.mark.asyncio
async def test_resume_of_a_one_shot_fires_now_instead_of_asking_croniter(monkeypatch):
    read_session = _FakeSession(_Result([{"cron_expr": None, "timezone": "UTC"}]))
    write_session = _FakeSession(_Result([_full_row(cron_expr=None)]))
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    before = datetime.datetime.now(datetime.timezone.utc)
    resp = await mod.resume_schedule("sched-1", _Auth(_ME))

    assert resp.cron_expr is None
    next_at = write_session.statements[0].compile().params["next_fire_at"]
    assert before <= next_at <= datetime.datetime.now(datetime.timezone.utc)


@pytest.mark.asyncio
async def test_patching_a_one_shot_does_not_invent_a_cron(monkeypatch):
    read_session = _FakeSession(
        _Result([{"task_type": "issue_wakeup", "cron_expr": None, "timezone": "UTC"}])
    )
    write_session = _FakeSession(_Result([_full_row(cron_expr=None)]))
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    await mod.update_schedule(
        "sched-1", mod.ScheduleUpdatePayload(timezone="Asia/Shanghai"), _Auth(_ME)
    )
    params = write_session.statements[0].compile().params
    assert "next_fire_at" not in params
