"""`/readyz` reports Postgres connection pressure but must never gate on it.

Two separate promises, and the second one is the load-bearing half:

1. the reading reaches the payload at all (otherwise the field is decoration);
2. **no value of that reading can change the verdict or the status code.**

Why (2) matters more than it looks. A 503 from `/readyz` is what triggers the
deploy chain's automatic rollback (`deploy-gpu.yml` → `up.sh` with the
previous images). Rolling back restarts containers, and restarting containers
opens *more* connection pools — the deploy window's double-pool overlap is
exactly what pushed the cluster to 106/100 in the first place. So gating on
connection pressure would make the alarm's own response feed the fire.

This is the same family as the `long_running` / `gates_readiness` lesson: a
signal worth reporting is not automatically a signal worth failing on, and the
cost of confusing the two is paid in automated restarts during an incident.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api.lifespan_router import readyz
from app.lifespan_helpers import BackgroundTaskRegistry


async def _forever() -> None:
    while True:  # daemon loop — mirrors reap_internal_queue
        await asyncio.sleep(3600)


async def _quick() -> None:
    await asyncio.sleep(0)


@pytest.fixture
async def ready_registry():
    """A registry in the 'all finite work done, daemons alive' state.

    Isolates the connection field: without it a 503 could come from startup
    state instead of from the thing under test.
    """
    registry = BackgroundTaskRegistry()
    registry.spawn("reaper", _forever(), long_running=True)
    seed_task = registry.spawn("seed", _quick())
    await asyncio.wait_for(asyncio.shield(seed_task), timeout=1.0)
    yield registry
    await registry.shutdown(timeout=1.0)


@pytest.fixture(autouse=True)
def _healthy_dbos(monkeypatch):
    """Hold the OTHER readiness input fixed, so any 503 seen below can only
    have come from the connection field."""
    monkeypatch.setenv("DBOS_DATABASE_URL", "")
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: False
    )


async def _call_readyz(registry: BackgroundTaskRegistry):
    """Drive the handler on the loop that owns the tasks (BackgroundTaskRegistry
    is single-loop only). Same stub-Request approach as test_readyz_dbos_gate."""
    from fastapi import Response

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(bg_tasks=registry))
    )
    response = Response()
    payload = await readyz(request, response)
    return response.status_code, payload


def _cached(monkeypatch, sample):
    async def _get():
        return sample

    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.get_cached_sample", _get
    )


@pytest.mark.parametrize("pressure_status", ["ok", "warning", "critical", "unknown"])
async def test_no_connection_reading_can_make_readyz_fail(
    ready_registry, monkeypatch, pressure_status
):
    """The mutation guard: every status, including ``critical``, stays 200.

    Parametrised over the full range on purpose. A version that gated only on
    ``critical`` — the tempting mistake, since it sounds severe — passes an
    "ok stays ready" test and fails only this one.
    """
    _cached(
        monkeypatch,
        {
            "status": pressure_status,
            "used": 198,
            "max_connections": 200,
            "percent": 99.0,
            "sampled_at": 0.0,
        },
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["status"] == "ready"
    # …and the reading is still reported, not suppressed to keep the gate green.
    assert payload["connections"]["status"] == pressure_status


async def test_the_reading_reaches_the_payload(ready_registry, monkeypatch):
    """Guard for the test above: if the field were missing entirely, the
    non-gating assertions would hold vacuously."""
    _cached(
        monkeypatch,
        {
            "status": "warning",
            "used": 165,
            "max_connections": 200,
            "percent": 82.5,
            "sampled_at": 123.0,
        },
    )

    _, payload = await _call_readyz(ready_registry)

    assert payload["connections"] == {
        "status": "warning",
        "used": 165,
        "max": 200,
        "percent": 82.5,
        "sampled_at": 123.0,
    }


async def test_a_missing_sample_reads_as_unknown_not_as_healthy(
    ready_registry, monkeypatch
):
    """A dead sampler must not look like a healthy database.

    ``None`` from the cache means "nobody is reporting" — rendering that as
    ``ok`` (or dropping the key) would make a broken monitor indistinguishable
    from a quiet one.
    """
    _cached(monkeypatch, None)

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["connections"]["status"] == "unknown"
    assert payload["connections"]["reason"] == "no recent sample"


async def test_a_broken_sampler_cannot_break_the_probe(ready_registry, monkeypatch):
    """The monitoring field rides along on the probe; it must not be able to
    take it down. A raising cache read degrades to ``unknown``, still 200."""

    async def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.get_cached_sample", _boom
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["connections"]["status"] == "unknown"


async def test_readyz_does_not_query_postgres(ready_registry, monkeypatch):
    """The probe reads the sampler's cache, never the database.

    A probe that opens a connection to report on connection exhaustion fails
    exactly when its reading matters most — and `/readyz` is hit on every
    container healthcheck tick, so it would add load proportional to the
    problem it is describing.
    """
    called = False

    async def _must_not_run(*_a, **_k):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.sample_connection_usage",
        _must_not_run,
    )
    _cached(monkeypatch, {"status": "ok", "used": 10, "max_connections": 200})

    await _call_readyz(ready_registry)

    assert called is False
