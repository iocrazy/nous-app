"""Sprint 5 — BoundsAdvertisement + BoundsRegistry."""
from __future__ import annotations

import time

import pytest

from app.agent_framework.bounds import BoundsAdvertisement, BoundsRegistry


def _bound(
    worker_id: str = "w-1",
    workflows: tuple[str, ...] = ("ai_transcription.run",),
    agents: tuple[str, ...] = ("script_ai",),
    role: str = "worker",
) -> BoundsAdvertisement:
    return BoundsAdvertisement(
        worker_id=worker_id,
        role=role,
        workflows=frozenset(workflows),
        agents=frozenset(agents),
        providers=frozenset({"qwen"}),
        lane_capacity={"chat": 5, "transcription": 2},
        version="abc123",
    )


@pytest.mark.unit
def test_bound_can_handle_workflow():
    b = _bound(workflows=("foo", "bar"))
    assert b.can_handle_workflow("foo") is True
    assert b.can_handle_workflow("baz") is False


@pytest.mark.unit
def test_bound_can_handle_agent():
    b = _bound(agents=("script_ai", "summarize"))
    assert b.can_handle_agent("script_ai") is True
    assert b.can_handle_agent("unknown_agent") is False


@pytest.mark.unit
def test_register_replaces_by_worker_id():
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-1", workflows=("v1",)))
    reg.register(_bound(worker_id="w-1", workflows=("v2",)))
    bounds = reg.live_bounds()
    assert len(bounds) == 1
    assert bounds[0].workflows == frozenset({"v2"})


@pytest.mark.unit
def test_unregister_removes_immediately():
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-1"))
    reg.register(_bound(worker_id="w-2"))
    reg.unregister("w-1")
    bounds = reg.live_bounds()
    assert len(bounds) == 1
    assert bounds[0].worker_id == "w-2"


@pytest.mark.unit
def test_heartbeat_keeps_bound_alive():
    """Stale-after window of 1s; heartbeat extends life."""
    reg = BoundsRegistry(stale_after_s=1.0)
    reg.register(_bound(worker_id="w-1"))

    now = time.time()
    # Pretend 0.5s passed — bound still live, heartbeat works.
    assert reg.heartbeat("w-1") is True
    assert len(reg.live_bounds(now=now + 0.5)) == 1

    # Pretend 2.0s passed without further heartbeat — pruned.
    assert len(reg.live_bounds(now=now + 2.0)) == 0


@pytest.mark.unit
def test_heartbeat_unknown_returns_false():
    """Caller should re-register if heartbeat fails (worker restart case)."""
    reg = BoundsRegistry()
    assert reg.heartbeat("never-registered") is False


@pytest.mark.unit
def test_workers_for_workflow_filters_by_capability():
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-A", workflows=("transcribe", "summarize")))
    reg.register(_bound(worker_id="w-B", workflows=("transcribe",)))
    reg.register(_bound(worker_id="w-C", workflows=("analyze",)))

    transcribers = reg.workers_for_workflow("transcribe")
    transcriber_ids = {b.worker_id for b in transcribers}
    assert transcriber_ids == {"w-A", "w-B"}

    analyzers = reg.workers_for_workflow("analyze")
    assert {b.worker_id for b in analyzers} == {"w-C"}


@pytest.mark.unit
def test_can_dispatch_workflow_false_when_no_worker():
    """The point of bounds — gateway pre-flight check."""
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-1", workflows=("transcribe",)))
    assert reg.can_dispatch_workflow("transcribe") is True
    assert reg.can_dispatch_workflow("video_analysis") is False


@pytest.mark.unit
def test_workers_for_agent():
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-A", agents=("script_ai", "summarize")))
    reg.register(_bound(worker_id="w-B", agents=("storyboard",)))

    script_workers = reg.workers_for_agent("script_ai")
    assert {b.worker_id for b in script_workers} == {"w-A"}


@pytest.mark.unit
def test_snapshot_includes_age():
    reg = BoundsRegistry()
    reg.register(_bound(worker_id="w-1"))
    now = time.time() + 5.0  # pretend 5s elapsed
    snap = reg.snapshot(now=now)
    assert "w-1" in snap
    assert snap["w-1"]["last_seen_age_s"] >= 4.5
    assert snap["w-1"]["role"] == "worker"
    assert "ai_transcription.run" in snap["w-1"]["workflows"]


@pytest.mark.unit
def test_snapshot_excludes_stale():
    """Stale bounds dropped before snapshot — admin UI sees only live workers."""
    reg = BoundsRegistry(stale_after_s=1.0)
    reg.register(_bound(worker_id="w-stale"))
    snap = reg.snapshot(now=time.time() + 2.0)
    assert "w-stale" not in snap


@pytest.mark.unit
def test_lane_capacity_carried_through():
    b = _bound()
    assert b.lane_capacity == {"chat": 5, "transcription": 2}
