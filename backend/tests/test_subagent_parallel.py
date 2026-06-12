"""M2-b: parallel sub-agent fan-out (Phase 4.5 — canvas plan).

``Skill(skill="task", tasks=[...])`` spawns N sub-agents concurrently,
capped by the caller agent's ``capability_profile.max_parallel_delegates``
(default 3). The single-task form is unchanged.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import (
    DEFAULT_MAX_PARALLEL,
    MAX_FANOUT,
    SubAgentTaskService,
)


def _service(**over) -> SubAgentTaskService:
    base = dict(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id="1",
        agent_depth=0,
    )
    base.update(over)
    return SubAgentTaskService(**base)


def _ok_envelope(i: int) -> dict:
    return {
        "summary": f"result {i}",
        "key_findings": [],
        "files_created": [],
        "tokens_used": 10,
        "sub_run_id": str(i),
        "status": "success",
    }


def _tasks(n: int) -> list[dict]:
    return [{"subagent_type": "researcher", "prompt": f"job {i}"} for i in range(n)]


# ============================================================
# Routing + aggregation
# ============================================================


@pytest.mark.asyncio
async def test_tasks_array_fans_out_and_aggregates_in_order() -> None:
    svc = _service()
    calls: list[str] = []

    async def fake_spawn(args):
        calls.append(args["prompt"])
        return _ok_envelope(len(calls))

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"tasks": _tasks(3)})

    assert result["status"] == "success"
    assert result["tasks_run"] == 3
    assert len(result["results"]) == 3
    # Results align with input order regardless of completion order.
    assert sorted(calls) == ["job 0", "job 1", "job 2"]


@pytest.mark.asyncio
async def test_concurrency_capped_by_max_parallel() -> None:
    svc = _service(max_parallel=2)
    active = 0
    peak = 0

    async def fake_spawn(args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _ok_envelope(0)

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"tasks": _tasks(6)})

    assert result["tasks_run"] == 6
    assert peak <= 2


@pytest.mark.asyncio
async def test_partial_failure_reports_partial_status() -> None:
    svc = _service()

    async def fake_spawn(args):
        if args["prompt"] == "job 1":
            return {**_ok_envelope(1), "status": "failed", "error": "boom"}
        return _ok_envelope(0)

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"tasks": _tasks(3)})

    assert result["status"] == "partial"
    statuses = [r["status"] for r in result["results"]]
    assert statuses.count("failed") == 1


@pytest.mark.asyncio
async def test_all_failed_reports_failed_status() -> None:
    svc = _service()

    async def fake_spawn(args):
        return {**_ok_envelope(0), "status": "failed", "error": "boom"}

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"tasks": _tasks(2)})
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_child_exception_becomes_failed_envelope() -> None:
    """A crashing child must not sink its siblings."""
    svc = _service()

    async def fake_spawn(args):
        if args["prompt"] == "job 0":
            raise RuntimeError("child crashed")
        return _ok_envelope(1)

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"tasks": _tasks(2)})

    assert result["status"] == "partial"
    assert result["results"][0]["status"] == "failed"
    assert result["results"][1]["status"] == "success"


# ============================================================
# Guard rails
# ============================================================


@pytest.mark.asyncio
async def test_empty_tasks_rejected() -> None:
    result = await _service().spawn({"tasks": []})
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_fanout_hard_cap() -> None:
    result = await _service().spawn({"tasks": _tasks(MAX_FANOUT + 1)})
    assert result["status"] == "failed"
    assert str(MAX_FANOUT) in result["error"]


@pytest.mark.asyncio
async def test_max_parallel_zero_rejects_parallel_form() -> None:
    result = await _service(max_parallel=0).spawn({"tasks": _tasks(2)})
    assert result["status"] == "failed"


def test_default_max_parallel() -> None:
    assert _service().max_parallel == DEFAULT_MAX_PARALLEL


@pytest.mark.asyncio
async def test_single_form_unchanged() -> None:
    """No ``tasks`` key → the existing single-spawn path handles it."""
    svc = _service()

    async def fake_spawn(args):
        return _ok_envelope(7)

    svc._spawn = fake_spawn  # type: ignore[method-assign]
    result = await svc.spawn({"subagent_type": "researcher", "prompt": "job"})
    assert result["status"] == "success"
    assert "results" not in result
