"""Readyz gating for DBOS orchestrator state.

2026-07-22 → 07-25: `DBOS_DATABASE_URL` pointed at `nous-db:5432` while the
self-hosted PG container actually listens on 55434, so the engine failed to come
up at every boot for three days. Every probe stayed green throughout — `/health`
is a hardcoded string, `/api/v1/healthz` is liveness-only, and `readyz` only
watched whether the reap/stall daemons were *alive* (they were: each tick caught
its own exception and looped, logging 1915 ERRORs). The outage surfaced only
when a user hand-clicked a transcribe and got HTTP 500 "DBOS orchestrator is
not enabled".

So readyz gates on DBOS too: a configured-but-dead orchestrator is degraded,
not ready. The gate is deliberately conditional on the DSN being set — an
intentionally DBOS-less deployment (empty `DBOS_DATABASE_URL`, a supported
state per docker/docker-compose.yml) must stay ready.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.api.lifespan_router import readyz
from app.lifespan_helpers import BackgroundTaskRegistry
from app.services.infra import dbos_orchestrator


def test_is_launched_is_false_when_singleton_only_instantiated(monkeypatch):
    """`is_enabled()` cannot detect a failed launch — hence `is_launched()`.

    `init_dbos()` assigns `_dbos = DBOS(config=cfg)` before touching the DB;
    `launch_dbos()` is what actually connects. A launch failure therefore
    leaves `_dbos` non-None, so `is_enabled()` (`_dbos or _client`) still
    reports True. That is the exact worker-role shape of the 2026-07-22
    outage, verified in a throwaway container: DSN on :5432, "singleton
    instantiated" logged, "DBOS orchestrator startup failed" logged, and
    readyz still answering 200 dbos=enabled.
    """
    monkeypatch.setattr(dbos_orchestrator, "_dbos", object())
    monkeypatch.setattr(dbos_orchestrator, "_launched", False)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)

    assert dbos_orchestrator.is_enabled() is True  # the trap
    assert dbos_orchestrator.is_launched() is False  # the honest signal


def test_is_launched_is_true_after_successful_launch(monkeypatch):
    monkeypatch.setattr(dbos_orchestrator, "_dbos", object())
    monkeypatch.setattr(dbos_orchestrator, "_launched", True)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)

    assert dbos_orchestrator.is_launched() is True


def test_is_launched_is_true_for_gateway_client(monkeypatch):
    """Gateway never calls launch_dbos — a constructed DBOSClient IS its
    readiness (construction connects, so failure leaves _client None)."""
    monkeypatch.setattr(dbos_orchestrator, "_dbos", None)
    monkeypatch.setattr(dbos_orchestrator, "_launched", False)
    monkeypatch.setattr(dbos_orchestrator, "_client", object())

    assert dbos_orchestrator.is_launched() is True


async def _forever() -> None:
    while True:  # daemon loop — mirrors reap_internal_queue
        await asyncio.sleep(3600)


async def _quick() -> None:
    await asyncio.sleep(0)


@pytest.fixture
async def ready_registry():
    """A registry in the 'all finite work done, daemons alive' state.

    Isolates the DBOS gate: without it, a 503 could come from either signal.
    """
    registry = BackgroundTaskRegistry()
    registry.spawn("reaper", _forever(), long_running=True)
    seed_task = registry.spawn("seed", _quick())
    await asyncio.wait_for(asyncio.shield(seed_task), timeout=1.0)
    yield registry
    await registry.shutdown(timeout=1.0)


async def _call_readyz(registry: BackgroundTaskRegistry):
    """Drive the readyz handler on the loop that owns the tasks.

    Same stub-Request approach as test_readyz_long_running: the handler only
    touches request.app.state, and BackgroundTaskRegistry is single-loop only.
    """
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(bg_tasks=registry))
    )
    response = Response()
    payload = await readyz(request, response)
    return response.status_code, payload


async def test_configured_but_disabled_dbos_is_degraded(
    ready_registry, monkeypatch
):
    """The 2026-07-22 outage: DSN set, init failed, every probe stayed green."""
    monkeypatch.setenv(
        "DBOS_DATABASE_URL", "postgresql://postgres:pw@nous-db:5432/postgres"
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: False
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 503
    assert payload["status"] == "degraded"
    assert payload["dbos"] == "configured_but_disabled"


async def test_enabled_dbos_is_ready(ready_registry, monkeypatch):
    monkeypatch.setenv(
        "DBOS_DATABASE_URL", "postgresql://postgres:pw@nous-db:55434/postgres"
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: True
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["dbos"] == "enabled"


async def test_unconfigured_dbos_stays_ready(ready_registry, monkeypatch):
    """Empty DSN is a supported 'DBOS disabled' deployment — must not 503."""
    monkeypatch.setenv("DBOS_DATABASE_URL", "")
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: False
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["dbos"] == "not_configured"


async def test_absent_dsn_env_stays_ready(ready_registry, monkeypatch):
    """Env var entirely absent behaves like empty, not like a failure."""
    monkeypatch.delenv("DBOS_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: False
    )

    status_code, payload = await _call_readyz(ready_registry)

    assert status_code == 200
    assert payload["dbos"] == "not_configured"


async def test_crashed_daemon_still_degrades_when_dbos_is_fine(
    monkeypatch,
):
    """The pre-existing daemon gate must survive the new DBOS gate."""
    monkeypatch.setenv(
        "DBOS_DATABASE_URL", "postgresql://postgres:pw@nous-db:55434/postgres"
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.is_launched", lambda: True
    )

    registry = BackgroundTaskRegistry()

    async def _crash() -> None:
        raise RuntimeError("daemon died at boot")

    daemon_task = registry.spawn("reaper", _crash(), long_running=True)
    with pytest.raises(RuntimeError):
        await daemon_task

    try:
        status_code, payload = await _call_readyz(registry)
        assert status_code == 503
        assert payload["status"] == "degraded"
    finally:
        await registry.shutdown(timeout=1.0)
