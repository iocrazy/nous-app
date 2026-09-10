"""``issue_wakeup`` fires — the DB-only half that runs inside the step.

``_fire_issue_wakeup`` decides and returns an ORDER; the workflow BODY
delivers it (``deliver_or_dispatch``). Everything asserted here is therefore
about what the step WRITES and what it HANDS BACK, never about delivery.

The session double records every UPDATE's compiled values keyed by the row id
in its WHERE clause, so "the schedule was disabled with this reason" is a
statement about the SQL that actually ran, not about a helper being called.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows import scheduled_master as sm

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rows: Optional[list] = None) -> None:
        self._rows = list(rows or [])

    def mappings(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeDb:
    """Records UPDATE values per row id; hands back queued read results."""

    def __init__(self, results: Optional[list] = None) -> None:
        self.updates: Dict[str, Dict[str, Any]] = {}
        self.statements: list = []
        self._results = list(results or [])

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        compiled = stmt.compile(dialect=postgresql.dialect())
        text = str(compiled)
        if text.lstrip().upper().startswith("UPDATE"):
            params = dict(compiled.params)
            sid = params.pop("id_1", None)
            if sid is not None:
                self.updates[str(sid)] = params
        return self._results.pop(0) if self._results else _Result()


class _Scope:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDb:
    db = _FakeDb()
    monkeypatch.setattr(db_session, "read_scope", lambda: _Scope(db))
    monkeypatch.setattr(db_session, "write_scope", lambda: _Scope(db))
    return db


FIRE_AT = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)


def _row(**kw: Any) -> Dict[str, Any]:
    base = {
        "id": "s1",
        "next_fire_at": FIRE_AT,
        "user_id": "u1",
        "task_type": "issue_wakeup",
        "cron_expr": None,
        "payload": {
            "issue_id": 7,
            "text": "ping",
            "once": True,
            "created_by": "user",
        },
    }
    return {**base, **kw}


def _issue(row: Optional[Dict[str, Any]]):
    async def _load(issue_id: int) -> Optional[Dict[str, Any]]:
        return row

    return _load


# ── _fire_issue_wakeup ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_issue_disables_the_schedule(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "done"}))
    assert await sm._fire_issue_wakeup(_row()) is None
    assert fake_db.updates["s1"]["enabled"] is False
    assert fake_db.updates["s1"]["pause_reason"] == "issue_terminal"
    # paused_at is the gate the Routines UI renders the reason behind (spec
    # §6 T1) — a row disabled without it is a silent stop.
    assert fake_db.updates["s1"]["paused_at"] is not None
    assert fake_db.updates["s1"]["skipped_count_1"] == 1


@pytest.mark.asyncio
async def test_hidden_issue_disables_the_schedule(monkeypatch, fake_db):
    monkeypatch.setattr(
        sm,
        "_load_issue",
        _issue({"status": "open", "hidden_at": datetime.now(timezone.utc)}),
    )
    assert await sm._fire_issue_wakeup(_row()) is None
    assert fake_db.updates["s1"]["enabled"] is False


@pytest.mark.asyncio
async def test_missing_issue_disables_the_schedule(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue(None))
    assert await sm._fire_issue_wakeup(_row()) is None
    assert fake_db.updates["s1"]["enabled"] is False


@pytest.mark.asyncio
async def test_live_issue_returns_an_order_and_writes_nothing(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    order = await sm._fire_issue_wakeup(_row())
    assert order["kind"] == "issue_wakeup"
    assert order["issue_id"] == 7
    assert order["text"] == "ping"
    assert order["user_id"] == "u1"
    assert order["source"] == {
        "kind": "schedule",
        "schedule_id": "s1",
        "created_by": "user",
    }
    # Deterministic per (schedule, scheduled instant) — and since a one-shot
    # is never advanced, a retry of the SAME wake-up reuses the same key.
    assert order["fire_key"] == f"sched:s1:{FIRE_AT.isoformat()}"
    assert "s1" not in fake_db.updates  # the step neither dispatches nor writes


@pytest.mark.asyncio
async def test_payload_as_json_string_is_still_read(monkeypatch, fake_db):
    """asyncpg may hand jsonb back as str — a wake-up must not become an
    'payload incomplete' error just because of the driver's shape."""
    import json

    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    order = await sm._fire_issue_wakeup(_row(payload=json.dumps(_row()["payload"])))
    assert order["issue_id"] == 7


@pytest.mark.asyncio
async def test_incomplete_payload_raises(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    with pytest.raises(RuntimeError):
        await sm._fire_issue_wakeup(_row(payload={"issue_id": 7, "text": "   "}))


# ── finish_issue_wakeup_step: the row is burned by the OUTCOME ──────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["inbox", "dispatched"])
async def test_a_delivered_wakeup_is_burned(fake_db, mode):
    await sm.finish_issue_wakeup_step("s1", "sched:s1:t0", mode, None)
    written = fake_db.updates["s1"]
    assert written["enabled"] is False
    assert written["pause_reason"] == "fired_once"
    assert written["paused_at"] is not None
    assert written["last_fired_at"] is not None
    assert written["last_error"] is None
    assert written["consecutive_fails"] == 0
    # fire_count is a SQL expression (col + 1), so it lands as a bind suffix.
    assert written["fire_count_1"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,reason",
    [("skipped", "dispatch_failed: dbos is down"), ("error", "wakeup failed: boom")],
)
async def test_a_failed_delivery_leaves_the_row_armed_and_books_the_failure(
    monkeypatch, mode, reason
):
    """The one thing that must never happen: a failed delivery that looks
    exactly like a delivered one. The row stays enabled so the next tick
    retries — the dedupe key is what makes that retry safe."""
    db = _FakeDb([_Result([1])])
    monkeypatch.setattr(db_session, "read_scope", lambda: _Scope(db))
    monkeypatch.setattr(db_session, "write_scope", lambda: _Scope(db))

    await sm.finish_issue_wakeup_step("s1", "sched:s1:t0", mode, reason)

    written = db.updates["s1"]
    assert "enabled" not in written
    assert written["last_error"] == reason
    assert "consecutive_fails" not in written  # a SQL expression, not a bind
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "consecutive_fails=(public.user_schedules.consecutive_fails +" in sql
    assert "fail_count=(public.user_schedules.fail_count +" in sql


@pytest.mark.asyncio
async def test_the_fifth_consecutive_failure_pauses_the_row(monkeypatch):
    """Same threshold the routine breaker uses — a wake-up whose delivery is
    permanently broken must stop retrying every minute."""
    db = _FakeDb([_Result([sm._AUTO_PAUSE_THRESHOLD])])
    monkeypatch.setattr(db_session, "read_scope", lambda: _Scope(db))
    monkeypatch.setattr(db_session, "write_scope", lambda: _Scope(db))

    await sm.finish_issue_wakeup_step("s1", "k", "skipped", "dispatch_failed: nope")

    assert db.updates["s1"]["enabled"] is False
    assert db.updates["s1"]["pause_reason"] == "dispatch_failed"
    assert db.updates["s1"]["paused_at"] is not None


@pytest.mark.asyncio
async def test_a_late_discovered_terminal_issue_disables_with_that_reason(fake_db):
    await sm.finish_issue_wakeup_step("s1", "k", "skipped", "issue_terminal")
    assert fake_db.updates["s1"]["enabled"] is False
    assert fake_db.updates["s1"]["pause_reason"] == "issue_terminal"


# ── _dispatch_one wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_one_returns_the_order_and_burns_nothing(monkeypatch, fake_db):
    """The step decides; the BODY delivers; only then is the row burned.
    Nothing about this row may change before delivery is known."""
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    row = _row(next_fire_at=datetime.now(timezone.utc), timezone="UTC")
    result = await sm._dispatch_one(row)
    assert result["outcome"] == "fired"
    assert result["order"]["kind"] == "issue_wakeup"
    assert fake_db.updates == {}, "no advance, no fired_once, no last_error clear"


@pytest.mark.asyncio
async def test_a_terminal_wakeup_never_reads_as_having_fired(monkeypatch, fake_db):
    """The terminal check runs BEFORE any advance, so a wake-up that could
    never land does not leave `fire_count 1` next to `issue_terminal`."""
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "done"}))
    row = _row(next_fire_at=datetime.now(timezone.utc), timezone="UTC")
    assert await sm._dispatch_one(row) == {"outcome": "skipped"}
    assert "fire_count" not in fake_db.updates["s1"]
    assert "last_fired_at" not in fake_db.updates["s1"]
    assert fake_db.updates["s1"]["pause_reason"] == "issue_terminal"


@pytest.mark.asyncio
async def test_a_cron_less_row_is_never_re_armed(monkeypatch, fake_db):
    """The whole point of the one-shot: no cron means nothing to compute a
    next fire from. Substituting "* * * * *" would re-arm it every minute."""
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    row = _row(next_fire_at=datetime.now(timezone.utc), timezone="UTC")
    await sm._dispatch_one(row)
    for stmt in fake_db.statements:
        assert "next_fire_at" not in dict(
            stmt.compile(dialect=postgresql.dialect()).params
        )


@pytest.mark.asyncio
async def test_stale_one_shot_is_disabled_not_advanced(monkeypatch, fake_db):
    """A one-shot missed by hours must not be silently re-scheduled to the
    next minute — there is no schedule to fall back to."""
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    row = _row(
        next_fire_at=datetime.now(timezone.utc) - timedelta(hours=5),
        timezone="UTC",
        stale_after_minutes=60,
    )
    result = await sm._dispatch_one(row)
    assert result == {"outcome": "skipped"}
    assert fake_db.updates["s1"]["enabled"] is False
    assert "next_fire_at" not in fake_db.updates["s1"]


# ── whitelist single source ─────────────────────────────────────────────────


def test_supported_task_types_covers_the_registry_and_the_two_inline_types():
    assert {"parse", "download", "transcode", "ai_summary"} <= sm.SUPPORTED_TASK_TYPES
    assert {"agent_routine", "issue_wakeup"} <= sm.SUPPORTED_TASK_TYPES
    assert "ai_transcription" not in sm.SUPPORTED_TASK_TYPES


@pytest.mark.asyncio
async def test_every_registry_task_type_resolves_to_a_callable():
    """The export must not drift from what _resolve_workflow_callable knows:
    a whitelist wider than the engine turns a schedule into a silent
    every-minute skip, the hardest 'configured but not in effect' to find."""
    for task_type in sm._REGISTRY_TASK_TYPES:
        assert await sm._resolve_workflow_callable(task_type) is not None


# ── the workflow body's fan-out ─────────────────────────────────────────────


def _order(**over: Any) -> Dict[str, Any]:
    base = {
        "kind": "issue_wakeup",
        "sched_id": "s1",
        "fire_key": "sched:s1:2026-09-11T09:00:00+00:00",
        "issue_id": 7,
        "text": "ping",
        "user_id": "u1",
        "source": {"kind": "schedule", "schedule_id": "s1", "created_by": "agent"},
    }
    base.update(over)
    return base


class _R:
    def __init__(self, mode: str, reason: Optional[str] = None) -> None:
        self.mode = mode
        self.reason = reason
        self.inbox_id = 1
        self.workflow_id = "w"


def _patch_delivery(monkeypatch, result, seen: Optional[list] = None):
    import app.services.issues.inbox_or_dispatch as iod

    async def _deliver(issue_id: int, **kw: Any):
        if seen is not None:
            seen.append((issue_id, kw))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(iod, "deliver_or_dispatch", _deliver)


def _patch_finish(monkeypatch) -> list:
    finished: list = []

    async def _finish(sched_id, fire_key, mode, reason=None):
        finished.append((sched_id, fire_key, mode, reason))

    monkeypatch.setattr(sm, "finish_issue_wakeup_step", _finish)
    return finished


@pytest.mark.asyncio
async def test_body_delivers_wakeup_orders_through_deliver_or_dispatch(monkeypatch):
    seen: list = []
    _patch_delivery(monkeypatch, _R("dispatched"), seen)
    finished = _patch_finish(monkeypatch)

    counters: Dict[str, Any] = {}
    order = _order()
    await sm._dispatch_routine_orders([order], counters)

    assert len(seen) == 1
    issue_id, kw = seen[0]
    assert issue_id == 7
    assert kw["kind"] == "steer"
    assert kw["content"] == {"text": "ping", "source": order["source"]}
    assert kw["message_body"] == "ping"
    assert kw["source"] == order["source"]
    assert kw["user_id"] == "u1"
    # The delivery is idempotent on the fire key — the body is not
    # step-recorded, so a crash replays it.
    assert kw["dedupe_key"] == order["fire_key"]
    assert counters.get("errors") is None
    assert finished == [("s1", order["fire_key"], "dispatched", None)]


@pytest.mark.asyncio
async def test_an_inbox_delivery_also_burns_the_row(monkeypatch):
    _patch_delivery(monkeypatch, _R("inbox"))
    finished = _patch_finish(monkeypatch)
    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders([_order()], counters)
    assert finished[0][2] == "inbox"
    assert counters.get("errors") is None


@pytest.mark.asyncio
async def test_a_typed_dispatch_failure_is_booked_like_an_exception(monkeypatch):
    """``deliver_or_dispatch`` does NOT always raise: its idle branch returns
    ``skipped/dispatch_failed`` as a value. Treating only the raise as a
    failure is how a lost wake-up came to look like a delivered one."""
    _patch_delivery(monkeypatch, _R("skipped", "dispatch_failed: dbos is down"))
    finished = _patch_finish(monkeypatch)

    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders([_order()], counters)

    assert counters["errors"] == 1
    assert finished[0][2] == "skipped"
    assert finished[0][3] == "dispatch_failed: dbos is down"


@pytest.mark.asyncio
async def test_a_non_failure_skip_is_not_counted_as_an_error(monkeypatch):
    _patch_delivery(monkeypatch, _R("skipped", "issue_terminal"))
    finished = _patch_finish(monkeypatch)

    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders([_order()], counters)

    assert counters.get("errors") is None
    assert finished[0][2:] == ("skipped", "issue_terminal")


@pytest.mark.asyncio
async def test_body_records_a_failed_wakeup_delivery(monkeypatch):
    _patch_delivery(monkeypatch, RuntimeError("nope"))
    finished = _patch_finish(monkeypatch)

    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders([_order()], counters)

    assert counters["errors"] == 1
    assert finished[0][2] == "error"
    assert "nope" in finished[0][3]


@pytest.mark.asyncio
async def test_a_replayed_body_delivers_once(monkeypatch):
    """A crash between the step and the body makes DBOS replay the body with
    the same orders. One wake-up must reach the issue, not two."""
    delivered: dict = {}

    import app.services.issues.inbox_or_dispatch as iod

    async def _deliver(issue_id: int, **kw: Any):
        key = kw["dedupe_key"]
        if key in delivered:
            return _R("inbox", "already_queued")
        delivered[key] = kw
        return _R("inbox")

    monkeypatch.setattr(iod, "deliver_or_dispatch", _deliver)
    _patch_finish(monkeypatch)

    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders([_order()], counters)
    await sm._dispatch_routine_orders([_order()], counters)

    assert len(delivered) == 1
    assert counters.get("errors") is None
