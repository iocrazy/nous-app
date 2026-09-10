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


def _row(**kw: Any) -> Dict[str, Any]:
    base = {
        "id": "s1",
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


@pytest.mark.asyncio
async def test_once_disables_after_firing(fake_db):
    await sm._finish_once(_row())
    assert fake_db.updates["s1"] == {"enabled": False, "pause_reason": "fired_once"}


# ── _dispatch_one wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_one_routes_issue_wakeup_and_finishes_once(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    row = _row(next_fire_at=datetime.now(timezone.utc), timezone="UTC")
    result = await sm._dispatch_one(row)
    assert result["outcome"] == "fired"
    assert result["order"]["kind"] == "issue_wakeup"
    # The row was advanced (fire_count/last_fired_at) AND then disabled by
    # _finish_once — the last UPDATE wins in the recording.
    assert fake_db.updates["s1"] == {"enabled": False, "pause_reason": "fired_once"}


@pytest.mark.asyncio
async def test_a_cron_less_row_is_never_re_armed(monkeypatch, fake_db):
    """The whole point of the one-shot: no cron means nothing to compute a
    next fire from. Substituting "* * * * *" would re-arm it every minute."""
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    row = _row(next_fire_at=datetime.now(timezone.utc), timezone="UTC")
    await sm._dispatch_one(row)
    advance = next(
        params
        for params in (
            dict(s.compile(dialect=postgresql.dialect()).params)
            for s in fake_db.statements
        )
        if "fire_count" in params
    )
    assert "next_fire_at" not in advance


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


@pytest.mark.asyncio
async def test_body_delivers_wakeup_orders_through_deliver_or_dispatch(monkeypatch):
    seen: list = []

    class _R:
        mode = "dispatched"
        reason = None

    async def _deliver(issue_id: int, **kw: Any):
        seen.append((issue_id, kw))
        return _R()

    import app.services.issues.inbox_or_dispatch as iod

    monkeypatch.setattr(iod, "deliver_or_dispatch", _deliver)

    counters: Dict[str, Any] = {}
    order = {
        "kind": "issue_wakeup",
        "sched_id": "s1",
        "issue_id": 7,
        "text": "ping",
        "user_id": "u1",
        "source": {"kind": "schedule", "schedule_id": "s1", "created_by": "agent"},
    }
    await sm._dispatch_routine_orders([order], counters)

    assert len(seen) == 1
    issue_id, kw = seen[0]
    assert issue_id == 7
    assert kw["kind"] == "steer"
    assert kw["content"] == {"text": "ping", "source": order["source"]}
    assert kw["message_body"] == "ping"
    assert kw["source"] == order["source"]
    assert kw["user_id"] == "u1"
    assert counters.get("errors") is None


@pytest.mark.asyncio
async def test_body_records_a_failed_wakeup_delivery(monkeypatch):
    async def _boom(issue_id: int, **kw: Any):
        raise RuntimeError("nope")

    import app.services.issues.inbox_or_dispatch as iod

    monkeypatch.setattr(iod, "deliver_or_dispatch", _boom)

    recorded: list = []

    async def _record(sched_id: str, err: str) -> None:
        recorded.append((sched_id, err))

    monkeypatch.setattr(sm, "record_routine_dispatch_error_step", _record)

    counters: Dict[str, Any] = {}
    await sm._dispatch_routine_orders(
        [
            {
                "kind": "issue_wakeup",
                "sched_id": "s1",
                "issue_id": 7,
                "text": "ping",
                "user_id": "u1",
                "source": {},
            }
        ],
        counters,
    )
    assert counters["errors"] == 1
    assert recorded and recorded[0][0] == "s1"
