"""D10-14 — PrometheusPusher: from_env + push_url + start/stop."""
from __future__ import annotations

import asyncio

import pytest

from app.agent_framework.prometheus_pusher import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_JOB,
    PrometheusPusher,
    from_env,
)
from app.agent_framework.telemetry import AgentMetrics


# ─── from_env ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_from_env_returns_none_without_url(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_PUSHGATEWAY_URL", raising=False)
    assert from_env(AgentMetrics()) is None


@pytest.mark.unit
def test_from_env_returns_none_for_empty_url(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "  ")
    assert from_env(AgentMetrics()) is None


@pytest.mark.unit
def test_from_env_constructs_with_url(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    p = from_env(AgentMetrics())
    assert isinstance(p, PrometheusPusher)
    assert p.gateway_url == "http://localhost:9091"
    assert p.job == DEFAULT_JOB


@pytest.mark.unit
def test_from_env_honors_overrides(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "http://gw:9091")
    monkeypatch.setenv("PROMETHEUS_PUSH_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("PROMETHEUS_PUSH_JOB", "test-job")
    monkeypatch.setenv("PROMETHEUS_PUSH_INSTANCE", "test-inst")
    p = from_env(AgentMetrics())
    assert p.interval_seconds == 60.0
    assert p.job == "test-job"
    assert p.instance == "test-inst"


# ─── push_url ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_push_url_format():
    p = PrometheusPusher(
        gateway_url="http://gw:9091",
        metrics=AgentMetrics(),
        job="myjob",
        instance="myinst",
    )
    assert p.push_url() == "http://gw:9091/metrics/job/myjob/instance/myinst"


@pytest.mark.unit
def test_push_url_strips_trailing_slash():
    p = PrometheusPusher(
        gateway_url="http://gw:9091/",
        metrics=AgentMetrics(),
        job="j",
        instance="i",
    )
    assert "/metrics/job/j/instance/i" in p.push_url()
    assert "//metrics" not in p.push_url()


@pytest.mark.unit
def test_default_instance_includes_pid():
    p = PrometheusPusher(
        gateway_url="http://gw", metrics=AgentMetrics(), job="j"
    )
    assert "pid" in p.instance


@pytest.mark.unit
def test_min_interval_clamped():
    """Sub-5s interval is too aggressive; clamp to 5."""
    p = PrometheusPusher(
        gateway_url="http://gw",
        metrics=AgentMetrics(),
        interval_seconds=1.0,
    )
    assert p.interval_seconds == 5.0


# ─── start / stop ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_stop_idempotent():
    """Start twice keeps one task; stop is safe even if never started."""
    p = PrometheusPusher(
        gateway_url="http://invalid",
        metrics=AgentMetrics(),
        interval_seconds=5.0,
    )
    await p.start()
    task1 = p._task
    await p.start()  # second start = no-op
    task2 = p._task
    assert task1 is task2
    await p.stop()
    await p.stop()  # second stop = no-op


@pytest.mark.asyncio
async def test_push_once_returns_false_on_unreachable_gateway():
    """No httpx connection → False, no exception."""
    p = PrometheusPusher(
        gateway_url="http://127.0.0.1:1",  # dead port
        metrics=AgentMetrics(),
        interval_seconds=5.0,
    )
    ok = await p._push_once()
    assert ok is False
