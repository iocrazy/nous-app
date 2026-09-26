"""``workflows.shots_backfill_sweep`` — the 10-minute backlog indexer (spec
2026-09-26 §3.3): the plan step's skip reasons, the body's dispatch loop,
the state row, and that the dispatch stays in the workflow body."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.repositories.video_shots_repository import ShotSweepRow
from app.services.library.shot_policy import (
    BackfillState,
    DispatchDecision,
    ShotsPolicy,
)
from app.workflows import shots_backfill_sweep as mod


def _decision(dispatch=True, reason="provider_local", *, policy=None, local=True):
    return DispatchDecision(
        dispatch=dispatch,
        reason=reason,
        policy=policy or ShotsPolicy("local_only", "local_only", 5, 200),
        provider_local=local,
        space_id=42 if dispatch else None,
    )


def _rows(n):
    return [
        ShotSweepRow(resource_id=100 + i, user_id=f"u-{i % 2}", title=f"v{i}")
        for i in range(n)
    ]


def _wire(
    monkeypatch,
    *,
    decision,
    active=0,
    state=None,
    pending=None,
    pending_fail=None,
):
    calls = SimpleNamespace(pending=[])

    async def _pending_all(**kwargs):
        calls.pending.append(kwargs)
        if pending_fail is not None:
            raise pending_fail
        rows = pending or []
        return rows[: kwargs["limit"]], len(rows)

    monkeypatch.setattr(
        "app.services.library.shot_policy.dispatch_decision",
        AsyncMock(return_value=decision),
    )
    monkeypatch.setattr(
        "app.services.library.shot_policy.read_backfill_state",
        AsyncMock(return_value=state or BackfillState("2026-09-26", 0)),
    )
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: SimpleNamespace(count_active_by_type=AsyncMock(return_value=active)),
    )
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shot_embeddings_repository",
        lambda: SimpleNamespace(pending_all=_pending_all),
    )
    return calls


# ---------------------------------------------------------------- plan ----
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision,expected",
    [
        (_decision(False, "policy_off"), "policy_off"),
        (_decision(False, "provider_not_local", local=False), "provider_not_local"),
        (_decision(False, "visual_space_unresolved"), "visual_space_unresolved"),
    ],
)
async def test_plan_skips_when_the_policy_says_no(monkeypatch, decision, expected):
    calls = _wire(monkeypatch, decision=decision, pending=_rows(3))
    plan = await mod.plan_shots_backfill_step()
    assert plan["skip"] == expected and "rows" not in plan
    assert calls.pending == [], "no pending read when nothing may run"


@pytest.mark.asyncio
async def test_plan_backpressure_holds_while_batch_are_active(monkeypatch):
    calls = _wire(monkeypatch, decision=_decision(), active=5, pending=_rows(3))
    plan = await mod.plan_shots_backfill_step()
    assert plan["skip"] == "backpressure" and calls.pending == []


@pytest.mark.asyncio
async def test_plan_fills_only_the_free_slots(monkeypatch):
    calls = _wire(monkeypatch, decision=_decision(), active=3, pending=_rows(9))
    plan = await mod.plan_shots_backfill_step()
    assert calls.pending[0]["limit"] == 2 and calls.pending[0]["space_id"] == 42
    assert calls.pending[0]["kind"] == "frame"
    assert [r["resource_id"] for r in plan["rows"]] == ["100", "101"]
    assert plan["rows"][0]["user_id"] == "u-0" and plan["pending_total"] == 9


@pytest.mark.asyncio
async def test_plan_daily_cap_applies_only_to_a_network_provider(monkeypatch):
    network = _decision(True, "policy_always", local=False)
    _wire(
        monkeypatch,
        decision=network,
        state=BackfillState("2026-09-26", 200),
        pending=_rows(3),
    )
    assert (await mod.plan_shots_backfill_step())["skip"] == "daily_cap"
    calls = _wire(
        monkeypatch,
        decision=network,
        state=BackfillState("2026-09-26", 198),
        pending=_rows(3),
    )
    plan = await mod.plan_shots_backfill_step()
    assert calls.pending[0]["limit"] == 2 and len(plan["rows"]) == 2
    calls = _wire(
        monkeypatch,
        decision=_decision(local=True),
        state=BackfillState("2026-09-26", 10_000),
        pending=_rows(3),
    )
    assert "skip" not in await mod.plan_shots_backfill_step()
    assert calls.pending[0]["limit"] == 5


@pytest.mark.asyncio
async def test_plan_reports_an_empty_backlog_and_a_missing_store(monkeypatch):
    _wire(monkeypatch, decision=_decision(), pending=[])
    plan = await mod.plan_shots_backfill_step()
    assert plan["skip"] == "nothing_pending" and plan["pending_total"] == 0
    _wire(monkeypatch, decision=_decision(), pending_fail=EmbeddingStoreMissing("507"))
    assert (await mod.plan_shots_backfill_step())["skip"] == "store_missing"


# ---------------------------------------------------------- dispatch loop ----
@pytest.mark.asyncio
async def test_dispatch_rows_keeps_going_after_one_failure(monkeypatch):
    seen = []

    async def _dispatch(**kw):
        seen.append(kw)
        if kw["resource_id"] == "101":
            raise RuntimeError("engine down")
        return "wf"

    monkeypatch.setattr(
        "app.services.library.shot_dispatch.dispatch_index_shots", _dispatch
    )
    rows = [
        {"resource_id": str(100 + i), "user_id": "u", "title": "t", "reason": "missing"}
        for i in range(3)
    ]
    dispatched, error = await mod._dispatch_rows(rows)
    assert dispatched == 2 and error.startswith("101: RuntimeError")
    assert all(kw["flow_id"] is None for kw in seen)


# ---------------------------------------------------------------- record ----
@pytest.mark.asyncio
async def test_record_adds_to_today_and_stamps_the_tick(monkeypatch):
    written = []
    monkeypatch.setattr(
        "app.services.library.shot_policy.read_backfill_state",
        AsyncMock(return_value=BackfillState("2026-09-26", 37, last_error="old")),
    )

    async def _write(state):
        written.append(state)

    monkeypatch.setattr("app.services.library.shot_policy.write_backfill_state", _write)
    monkeypatch.setattr(
        "app.services.library.shot_policy.now_utc_iso", lambda: "2026-09-26T10:20:00"
    )
    await mod.record_shots_backfill_step(dispatched=5, error=None, skip=None)
    (state,) = written
    assert state.dispatched_today == 42 and state.last_tick == "2026-09-26T10:20:00"
    assert state.last_error is None and state.last_skip is None


# ------------------------------------------------------------- the body ----
def test_body_plans_in_a_step_dispatches_in_the_body_records_in_a_step():
    body = inspect.getsource(inspect.unwrap(mod.shots_backfill_sweep_workflow))
    assert "await plan_shots_backfill_step()" in body
    assert "await _dispatch_rows(" in body
    assert "await record_shots_backfill_step(" in body
    # The dispatch helper starts a workflow: never from inside a step.
    for step in (mod.plan_shots_backfill_step, mod.record_shots_backfill_step):
        assert "dispatch_index_shots" not in inspect.getsource(inspect.unwrap(step))


def test_registered_in_the_worker_only_bundle():
    import app.workflows._scheduled_bundle as bundle

    assert bundle.shots_backfill_sweep_workflow is mod.shots_backfill_sweep_workflow
    assert '@DBOS.scheduled("*/10 * * * *")' in inspect.getsource(mod)


@pytest.mark.asyncio
async def test_workflow_body_end_to_end(monkeypatch):
    plan = {
        "rows": [
            {"resource_id": "7", "user_id": "u", "title": "t", "reason": "missing"}
        ],
        "pending_total": 1,
    }
    monkeypatch.setattr(mod, "plan_shots_backfill_step", AsyncMock(return_value=plan))
    recorded = AsyncMock()
    monkeypatch.setattr(mod, "record_shots_backfill_step", recorded)
    dispatch = AsyncMock(return_value="wf-1")
    monkeypatch.setattr(
        "app.services.library.shot_dispatch.dispatch_index_shots", dispatch
    )
    await inspect.unwrap(mod.shots_backfill_sweep_workflow)(None, None)
    dispatch.assert_awaited_once_with(
        user_id="u", resource_id="7", title="t", flow_id=None
    )
    recorded.assert_awaited_once_with(dispatched=1, error=None, skip=None)
