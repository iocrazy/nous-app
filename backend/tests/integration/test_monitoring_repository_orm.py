"""Integration tests for MonitoringRepositoryOrm (Phase 2 admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND strategy-C parity for the three log
tables the admin monitoring dashboard reads:

  - api_request_logs.timestamp / application_logs.logged_at (tstz) → ISO STR
    (CONSUMED — router does ts.replace("Z",..)+fromisoformat / sets logged_at:str)
  - status_code / response_time_ms (int) → native int
  - frontend_error_count → native int (COUNT — 5.3 trap)

Column-SUBSET selects (not SELECT *). Date-window filters bind tz-aware datetimes.
Reads only.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_monitoring_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "/__test_orm_mon_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def cleanup_test_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM api_request_logs WHERE path LIKE $1", _PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM application_logs WHERE message LIKE $1", _PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM frontend_error_logs WHERE error_type LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _seed_request(conn, when, **overrides):
    payload = {
        "request_id": uuid.uuid4().hex[:36],
        "method": "GET",
        "path": f"{_PREFIX}{uuid.uuid4().hex[:6]}",
        "status_code": 200,
        "response_time_ms": 42,
        "timestamp": when,
    }
    payload.update(overrides)
    cols = list(payload.keys())
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(
        f"INSERT INTO api_request_logs ({col_list}) VALUES ({ph})", *payload.values()
    )


async def _seed_app(conn, when, **overrides):
    payload = {
        "level": "ERROR",
        "module": "app.test.module",
        "message": f"{_PREFIX}{uuid.uuid4().hex[:6]}",
        "logged_at": when,
    }
    payload.update(overrides)
    cols = list(payload.keys())
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(
        f"INSERT INTO application_logs ({col_list}) VALUES ({ph})", *payload.values()
    )


async def _seed_fe(conn, when):
    await conn.execute(
        "INSERT INTO frontend_error_logs (error_type, created_at) VALUES ($1, $2)",
        f"{_PREFIX}{uuid.uuid4().hex[:6]}",
        when,
    )


def _repo():
    from app.repositories.admin.monitoring_repository_orm import (
        MonitoringRepositoryOrm,
    )

    return MonitoringRepositoryOrm()


async def test_request_logs_between_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_request(conn, now, status_code=503, response_time_ms=999)
    finally:
        await conn.close()

    rows = await _repo().request_logs_between(start, end)
    ours = [r for r in rows if r["path"].startswith(_PREFIX)]
    assert ours
    r = ours[0]
    assert type(r["timestamp"]) is str
    # The router calls ts.replace("Z","+00:00") then fromisoformat — must be ISO.
    assert datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    assert type(r["status_code"]) is int and r["status_code"] == 503
    assert type(r["response_time_ms"]) is int and r["response_time_ms"] == 999
    assert set(r.keys()) == {
        "path",
        "method",
        "status_code",
        "response_time_ms",
        "timestamp",
    }


async def test_request_logs_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Pins the timestamp >= start / <= end boundary (tz-aware bind)."""
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_request(conn, now - timedelta(minutes=30), path=f"{_PREFIX}in")
        await _seed_request(conn, now - timedelta(hours=2), path=f"{_PREFIX}out")
    finally:
        await conn.close()

    rows = await _repo().request_logs_between(start, end)
    paths = {r["path"] for r in rows}
    assert f"{_PREFIX}in" in paths
    assert f"{_PREFIX}out" not in paths


async def test_app_logs_between_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_app(conn, now, level="ERROR")
    finally:
        await conn.close()

    rows = await _repo().app_logs_between(start, end)
    ours = [r for r in rows if r["message"].startswith(_PREFIX)]
    assert ours
    r = ours[0]
    assert type(r["logged_at"]) is str  # RecentErrorEntry.logged_at:str consumer
    assert datetime.fromisoformat(r["logged_at"])
    assert set(r.keys()) == {"level", "module", "message", "logged_at"}


async def test_frontend_error_count_native_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_fe(conn, now)
        await _seed_fe(conn, now)
        # one outside the window → not counted
        await _seed_fe(conn, now - timedelta(hours=5))
    finally:
        await conn.close()

    count = await _repo().frontend_error_count(start, end)
    assert type(count) is int
    assert count >= 2


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import monitoring_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_MONITORING", False)
    repo = mod.get_monitoring_repository()
    assert type(repo) is mod.MonitoringRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import monitoring_repository as mod
    from app.repositories.admin.monitoring_repository_orm import (
        MonitoringRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_MONITORING", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_monitoring_repository(), MonitoringRepositoryOrm)
