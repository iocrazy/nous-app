"""``ScheduleWakeup`` — the agent arming its own one-shot wake-up.

Every rejection here is a tool RESULT the model reads and can act on, never an
exception: a raise would end the turn on something the agent could have fixed
by asking for a different time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.ai.tools import schedule_wakeup_tool as swt

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self, run_id: int = 4242) -> None:
        self.run_id = run_id
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type: str, payload: dict, **kw: Any) -> None:
        self.events.append((event_type, payload))


@pytest.fixture
def inserted(monkeypatch: pytest.MonkeyPatch) -> list:
    """Capture the row the tool would insert instead of touching a DB, and
    answer the per-run count from those same rows — the cap is counted in the
    DATABASE now, so the double must model the table, not a counter."""
    rows: list = []

    async def _insert(row: dict) -> str:
        rows.append(row)
        return f"sched-uuid-{len(rows)}"

    async def _count(run_id: str) -> int:
        return sum(1 for r in rows if r["payload"].get("run_id") == run_id)

    monkeypatch.setattr(swt, "_insert_wakeup_row", _insert)
    monkeypatch.setattr(swt, "_count_wakeups_for_run", _count)
    return rows


def _handler():
    return swt.make_schedule_wakeup_handler(issue_id=7, user_id="u1")


# ── the spec the model sees ─────────────────────────────────────────────────


def test_spec_is_a_function_with_the_three_documented_parameters():
    spec = swt.schedule_wakeup_spec()
    assert spec["type"] == "function"
    fn = spec["function"]
    assert fn["name"] == "ScheduleWakeup"
    assert set(fn["parameters"]["properties"]) == {"at", "delay_minutes", "note"}
    assert fn["parameters"]["required"] == ["note"]


# ── happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delay_minutes_arms_a_row_and_emits_schedule_set(inserted):
    recorder = _Recorder()
    before = datetime.now(timezone.utc)
    out = await _handler()({"delay_minutes": 60, "note": "check the render"}, recorder)

    assert out["schedule_id"] == "sched-uuid-1"
    fire_at = datetime.fromisoformat(out["fire_at"])
    assert before + timedelta(minutes=59) < fire_at < before + timedelta(minutes=61)

    row = inserted[0]
    assert row["task_type"] == "issue_wakeup"
    assert row["cron_expr"] is None
    assert row["user_id"] == "u1"
    assert row["next_fire_at"] == fire_at
    assert row["payload"] == {
        "issue_id": 7,
        "text": "check the render",
        "once": True,
        "created_by": "agent",
        "run_id": "4242",
    }

    assert recorder.events == [
        (
            "schedule_set",
            {
                "schedule_id": "sched-uuid-1",
                "fire_at": out["fire_at"],
                "note": "check the render",
            },
        )
    ]


@pytest.mark.asyncio
async def test_at_wins_over_delay_minutes(inserted):
    at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    out = await _handler()(
        {"at": at, "delay_minutes": 5, "note": "tomorrow"}, _Recorder()
    )
    assert out["fire_at"] == datetime.fromisoformat(at).isoformat()


# ── typed refusals ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_neither_at_nor_delay_is_a_typed_error(inserted):
    out = await _handler()({"note": "sometime"}, _Recorder())
    assert out == {"error": "at or delay_minutes required"}
    assert inserted == []


@pytest.mark.asyncio
async def test_beyond_thirty_days_is_refused(inserted):
    at = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
    out = await _handler()({"at": at, "note": "much later"}, _Recorder())
    assert out == {"error": "fire_at must be within 30 days"}
    assert inserted == []


@pytest.mark.asyncio
async def test_a_time_in_the_past_is_refused(inserted):
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    out = await _handler()({"at": at, "note": "yesterday"}, _Recorder())
    assert out["error"] == "fire_at must be in the future"
    assert inserted == []


@pytest.mark.asyncio
async def test_unparseable_at_is_refused(inserted):
    out = await _handler()({"at": "next tuesday", "note": "soon"}, _Recorder())
    assert "ISO-8601" in out["error"]
    assert inserted == []


@pytest.mark.asyncio
async def test_a_naive_at_is_read_as_utc(inserted):
    """A model that omits the offset means "UTC", not "crash"."""
    naive = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    out = await _handler()({"at": naive.isoformat(), "note": "later"}, _Recorder())
    assert datetime.fromisoformat(out["fire_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_an_empty_note_is_refused(inserted):
    out = await _handler()({"delay_minutes": 10, "note": "  "}, _Recorder())
    assert out == {"error": "note is required"}
    assert inserted == []


@pytest.mark.asyncio
async def test_the_fourth_wakeup_in_one_run_is_refused(inserted):
    handler = _handler()
    recorder = _Recorder()
    for _ in range(swt.MAX_WAKEUPS_PER_RUN):
        assert "schedule_id" in await handler(
            {"delay_minutes": 30, "note": "again"}, recorder
        )
    out = await handler({"delay_minutes": 30, "note": "once more"}, recorder)
    assert out == {"error": "too_many_wakeups"}
    assert len(inserted) == swt.MAX_WAKEUPS_PER_RUN


@pytest.mark.asyncio
async def test_the_cap_is_per_run_not_per_turn(inserted):
    """A run spans several turns and each turn builds a FRESH handler, so a
    counter living in the closure caps turns, not runs — 5 turns would arm 15
    wake-ups. The count comes from the rows this run already armed."""
    recorder = _Recorder()
    for _ in range(swt.MAX_WAKEUPS_PER_RUN):
        assert "schedule_id" in await _handler()(
            {"delay_minutes": 30, "note": "again"}, recorder
        )
    out = await _handler()({"delay_minutes": 30, "note": "next turn"}, recorder)
    assert out == {"error": "too_many_wakeups"}


@pytest.mark.asyncio
async def test_another_run_starts_from_zero(inserted):
    for _ in range(swt.MAX_WAKEUPS_PER_RUN):
        await _handler()({"delay_minutes": 30, "note": "again"}, _Recorder(1))
    assert "schedule_id" in await _handler()(
        {"delay_minutes": 30, "note": "other run"}, _Recorder(2)
    )


@pytest.mark.asyncio
async def test_a_naive_fire_at_has_its_own_code_on_the_api_path():
    """The tool reads a naive time as UTC; the API refuses it — and must say
    WHICH thing is wrong, or the UI shows "pick a time" to someone who did.

    Asserted through the real request path and the real error envelope: the
    browser reads `details.code`, and a string detail would arrive there as a
    bare `http_400` no matter what the raise site intended."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.schedules_router import router
    from app.core.deps import get_auth
    from app.core.exceptions import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")

    class _Auth:
        user_id = "11111111-1111-4111-8111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    naive = (datetime.now() + timedelta(hours=2)).isoformat()  # no offset
    resp = TestClient(app).post(
        "/api/v1/schedules",
        json={
            "task_type": "issue_wakeup",
            "fire_at": naive,
            "payload": {"issue_id": 123, "text": "check the render"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["details"]["code"] == "fire_at_timezone_required"


@pytest.mark.asyncio
async def test_a_refused_call_does_not_consume_the_budget(inserted):
    handler = _handler()
    recorder = _Recorder()
    for _ in range(5):
        await handler({"note": "no time given"}, recorder)
    assert "schedule_id" in await handler({"delay_minutes": 5, "note": "ok"}, recorder)


@pytest.mark.asyncio
async def test_a_failed_insert_is_a_typed_result_not_a_raise(monkeypatch):
    async def _boom(row: dict) -> str:
        raise RuntimeError("db down")

    monkeypatch.setattr(swt, "_insert_wakeup_row", _boom)
    out = await _handler()({"delay_minutes": 5, "note": "ok"}, _Recorder())
    assert out["error"].startswith("ScheduleWakeup failed")


# ── the horizon has one owner ───────────────────────────────────────────────


def test_the_api_and_the_tool_share_one_horizon():
    """The agent's ceiling and the user's ceiling are the same number, or one
    of the two paths silently allows what the other rejects."""
    from app.api.schedules_router import MAX_WAKEUP_HORIZON

    assert MAX_WAKEUP_HORIZON is swt.MAX_WAKEUP_HORIZON
