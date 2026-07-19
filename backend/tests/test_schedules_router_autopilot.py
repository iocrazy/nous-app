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
