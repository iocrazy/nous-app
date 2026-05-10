"""I4 — Redis-backed BoundsRegistry: serialization + remote apply."""

from __future__ import annotations

import json

import pytest

from app.agent_framework.bounds import BoundsAdvertisement
from app.agent_framework.bounds_redis import (
    CHANNEL_REGISTER,
    CHANNEL_UNREGISTER,
    RedisBoundsRegistry,
    _deserialize_bound,
    _serialize_bound,
)


def _bound(wid="w-1") -> BoundsAdvertisement:
    return BoundsAdvertisement(
        worker_id=wid,
        role="worker",
        workflows=frozenset({"download_workflow", "transcode_workflow"}),
        agents=frozenset({"script_ai"}),
        providers=frozenset({"qwen"}),
        lane_capacity={"chat": 5},
        version="abc123",
    )


# ─── Serialization round-trip ────────────────────────────────────────


@pytest.mark.unit
def test_serialize_deserialize_roundtrip():
    b = _bound()
    payload = _serialize_bound(b)
    out = _deserialize_bound(payload)
    assert out is not None
    assert out.worker_id == b.worker_id
    assert out.role == b.role
    assert out.workflows == b.workflows
    assert out.agents == b.agents
    assert out.providers == b.providers
    assert out.lane_capacity == b.lane_capacity
    assert out.version == b.version


@pytest.mark.unit
def test_deserialize_garbled_returns_none():
    assert _deserialize_bound("not json") is None
    assert _deserialize_bound("{}") is None  # missing required fields


@pytest.mark.unit
def test_deserialize_partial_missing_optional_fields():
    """Forward-compat: extra fields ignored, missing optionals default."""
    minimal = json.dumps({"worker_id": "w", "role": "worker"})
    out = _deserialize_bound(minimal)
    assert out is not None
    assert out.workflows == frozenset()
    assert out.providers == frozenset()


# ─── Local-only mode (no redis client) ────────────────────────────────


@pytest.mark.unit
def test_no_redis_falls_back_to_in_process():
    """redis_client=None → all writes touch local registry only."""
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg.register(_bound())
    assert len(reg.live_bounds()) == 1
    assert reg.can_dispatch_workflow("download_workflow")
    reg.unregister("w-1")
    assert len(reg.live_bounds()) == 0


# ─── Remote-message application ─────────────────────────────────────


@pytest.mark.unit
def test_apply_remote_register_adds_to_local():
    reg = RedisBoundsRegistry.create(redis_client=None)
    payload = _serialize_bound(_bound("w-remote"))
    reg._apply_remote(CHANNEL_REGISTER, payload)
    bounds = reg.live_bounds()
    assert any(b.worker_id == "w-remote" for b in bounds)


@pytest.mark.unit
def test_apply_remote_unregister_removes_from_local():
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg.register(_bound("w-1"))
    reg._apply_remote(CHANNEL_UNREGISTER, "w-1")
    assert not any(b.worker_id == "w-1" for b in reg.live_bounds())


@pytest.mark.unit
def test_apply_remote_heartbeat_message_refreshes():
    """{_hb: true} → just heartbeat, don't try to deserialize."""
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg.register(_bound("w-hb"))
    payload = json.dumps({"worker_id": "w-hb", "_hb": True})
    reg._apply_remote(CHANNEL_REGISTER, payload)
    assert any(b.worker_id == "w-hb" for b in reg.live_bounds())


@pytest.mark.unit
def test_apply_remote_unknown_channel_noop():
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg._apply_remote("bounds:unknown", _serialize_bound(_bound()))
    assert reg.live_bounds() == []


@pytest.mark.unit
def test_apply_remote_garbled_register_noop():
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg._apply_remote(CHANNEL_REGISTER, "garbage")
    assert reg.live_bounds() == []


@pytest.mark.unit
def test_apply_remote_none_channel_noop():
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg._apply_remote(None, "anything")
    assert reg.live_bounds() == []


# ─── Read API delegates to local ─────────────────────────────────────


@pytest.mark.unit
def test_read_api_delegates():
    reg = RedisBoundsRegistry.create(redis_client=None)
    reg.register(_bound())
    workflow_workers = reg.workers_for_workflow("download_workflow")
    assert len(workflow_workers) == 1
    agent_workers = reg.workers_for_agent("script_ai")
    assert len(agent_workers) == 1
    assert reg.can_dispatch_workflow("download_workflow")
    assert not reg.can_dispatch_workflow("nonexistent_workflow")
    snap = reg.snapshot()
    assert "w-1" in snap
