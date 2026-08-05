"""W2a autopilot hardening — master scheduler unit tests.

Covers the pure helpers (timezone-aware next-fire, stale detection) and the
consecutive-failure state machine (reset on success, untouched by skips,
auto-pause at threshold) with a faked ORM session — no real DB.

ORM (Phase B4): all user_schedules reads/writes moved from raw
db_engine.fetch_all/execute calls to SQLAlchemy Core through
app.db.session.read_scope()/write_scope(). The harness patches those scopes
with a recording session and inspects the compiled statement instead of the
raw SQL string, mirroring tests/test_orm_b3_task1_compile_coverage.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows import scheduled_master as sm

# ── timezone-aware next-fire ────────────────────────────────────────────────


def test_compute_next_fire_is_tz_aware_and_dst_safe():
    """ "0 9 * * *" in America/New_York must mean 9am *local* — the stored UTC
    time converts back to 9:00 wall-clock regardless of DST season."""
    next_utc = sm._compute_next_fire("0 9 * * *", "America/New_York")
    # Stored as UTC.
    assert next_utc.tzinfo is not None
    assert next_utc.utcoffset() == timedelta(0)
    # Converts back to 9:00 New York wall-clock (DST-safe by construction).
    ny = next_utc.astimezone(ZoneInfo("America/New_York"))
    assert (ny.hour, ny.minute) == (9, 0)
    # And it's genuinely in the future.
    assert next_utc > datetime.now(timezone.utc)
    # The timezone is actually applied: 9am NY is 13:00 (EDT) or 14:00 (EST)
    # UTC — never the naive 09:00 a UTC interpretation would give.
    assert next_utc.hour in (13, 14)


def test_compute_next_fire_utc_matches_wall_clock():
    next_utc = sm._compute_next_fire("0 9 * * *", "UTC")
    assert next_utc.hour == 9 and next_utc.minute == 0


def test_compute_next_fire_invalid_tz_falls_back_to_utc():
    # Unknown tz → UTC, so a UTC cron hour comes back unchanged.
    next_utc = sm._compute_next_fire("0 9 * * *", "Not/AZone")
    assert next_utc.hour == 9


def test_compute_next_fire_invalid_cron_falls_back_plus_1h():
    before = datetime.now(timezone.utc)
    nxt = sm._compute_next_fire("not a cron expr", "UTC")
    assert nxt > before
    assert nxt <= datetime.now(timezone.utc) + timedelta(hours=1, seconds=5)


# ── stale detection ─────────────────────────────────────────────────────────


def test_is_stale_boundaries():
    now = datetime.now(timezone.utc)
    assert sm._is_stale(now - timedelta(minutes=90), now, 60) is True
    assert sm._is_stale(now - timedelta(minutes=30), now, 60) is False
    # No prior time / disabled threshold → never stale.
    assert sm._is_stale(None, now, 60) is False
    assert sm._is_stale(now - timedelta(minutes=90), now, 0) is False
    # Naive datetime (defensive) is treated as UTC, not crashed on.
    assert (
        sm._is_stale((now - timedelta(minutes=90)).replace(tzinfo=None), now, 60)
        is True
    )


# ── ORM session harness (mirrors test_orm_b3_task1_compile_coverage.py) ─────


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    def __init__(self, calls: list[Any], results: list[_FakeResult] | None = None):
        self.calls = calls
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return self._results.pop(0) if self._results else self._default


class _ScopeCM:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    calls: list[Any] = []
    session = _RecordingSession(calls, results)
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))
    return session


# ── consecutive-failure bookkeeping ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_dispatch_failure_increments_without_pause(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _patch_scopes(monkeypatch)
    paused = await sm._record_dispatch_failure(
        {"id": "s1", "consecutive_fails": 2}, "boom"
    )
    assert paused is False
    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "user_schedules" in sql
    assert binds["consecutive_fails"] == 3
    assert "enabled" not in binds
    assert "paused_at" not in binds


@pytest.mark.asyncio
async def test_record_dispatch_failure_auto_pauses_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _patch_scopes(monkeypatch)
    # 4 + 1 == 5 == _AUTO_PAUSE_THRESHOLD.
    paused = await sm._record_dispatch_failure(
        {"id": "s1", "consecutive_fails": 4}, "boom" * 200
    )
    assert paused is True
    sql, binds = session.calls[0]
    assert binds["enabled"] is False
    assert "paused_at" in binds
    assert binds["consecutive_fails"] == 5
    assert binds["pause_reason"].startswith("auto-paused after 5 consecutive failures")
    assert len(binds["pause_reason"]) <= 500  # truncated


@pytest.mark.asyncio
async def test_reset_consecutive_fails_skips_when_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _patch_scopes(monkeypatch)
    await sm._reset_consecutive_fails({"id": "s1", "consecutive_fails": 0})
    assert session.calls == []  # no needless write


@pytest.mark.asyncio
async def test_reset_consecutive_fails_clears_when_nonzero(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _patch_scopes(monkeypatch)
    await sm._reset_consecutive_fails({"id": "s1", "consecutive_fails": 3})
    assert len(session.calls) == 1
    _sql, binds = session.calls[0]
    assert binds["consecutive_fails"] == 0


# ── stale discard through _dispatch_one ─────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_one_stale_discard_increments_skipped_not_fired(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _patch_scopes(monkeypatch)
    now = datetime.now(timezone.utc)
    row = {
        "id": "s1",
        "task_type": "parse",
        "payload": {},
        "user_id": None,
        "timezone": "UTC",
        "cron_expr": "0 9 * * *",
        "next_fire_at": now - timedelta(hours=5),  # long past → stale
        "stale_after_minutes": 60,
    }
    result = await sm._dispatch_one(row)
    assert result == {"outcome": "skipped"}
    # Exactly one UPDATE: advance + skipped_count++. No fire_count bump, no
    # dispatch.
    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "skipped_count=(public.user_schedules.skipped_count +" in sql
    assert "fire_count" not in binds


# ── fire_due_schedules_step state machine ───────────────────────────────────


def _configure_due_rows(monkeypatch, rows):
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)
    _patch_scopes(monkeypatch, [_FakeResult(rows=rows)])


@pytest.mark.asyncio
async def test_fire_due_resets_consecutive_on_success(monkeypatch):
    _configure_due_rows(monkeypatch, [{"id": "s1", "consecutive_fails": 3}])
    resets: list = []

    async def fake_reset(row):
        resets.append(row["id"])

    async def fake_dispatch(row):
        return {"outcome": "fired", "order": None}

    monkeypatch.setattr(sm, "_reset_consecutive_fails", fake_reset)
    monkeypatch.setattr(sm, "_dispatch_one", fake_dispatch)

    counters = await sm.fire_due_schedules_step()
    assert counters["fired"] == 1
    assert counters["skipped"] == 0
    assert counters["errors"] == 0
    assert resets == ["s1"]  # success clears the failure run


@pytest.mark.asyncio
async def test_fire_due_skip_does_not_reset_consecutive(monkeypatch):
    _configure_due_rows(monkeypatch, [{"id": "s1", "consecutive_fails": 3}])
    resets: list = []

    async def fake_reset(row):
        resets.append(row["id"])

    async def fake_dispatch(row):
        return {"outcome": "skipped"}

    monkeypatch.setattr(sm, "_reset_consecutive_fails", fake_reset)
    monkeypatch.setattr(sm, "_dispatch_one", fake_dispatch)

    counters = await sm.fire_due_schedules_step()
    assert counters["skipped"] == 1
    assert counters["fired"] == 0
    assert resets == []  # a skip must NOT touch the failure run


@pytest.mark.asyncio
async def test_fire_due_failure_records_and_pauses(monkeypatch):
    _configure_due_rows(monkeypatch, [{"id": "s1", "consecutive_fails": 4}])
    recorded: list = []

    async def fake_dispatch(row):
        raise RuntimeError("dispatch boom")

    async def fake_record(row, err):
        recorded.append((row["id"], err))
        return True  # signals auto-pause

    monkeypatch.setattr(sm, "_dispatch_one", fake_dispatch)
    monkeypatch.setattr(sm, "_record_dispatch_failure", fake_record)

    counters = await sm.fire_due_schedules_step()
    assert counters["errors"] == 1
    assert counters["paused"] == 1
    assert recorded and recorded[0][0] == "s1"
    assert "dispatch boom" in recorded[0][1]


@pytest.mark.asyncio
async def test_fire_due_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.db.engine.is_configured", lambda: False)
    counters = await sm.fire_due_schedules_step()
    assert counters == {"due": 0, "fired": 0, "skipped": 0, "errors": 0}
