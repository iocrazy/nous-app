"""Integration tests for AdminSearchRepositoryOrm (Phase 2 admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND strategy-C parity for the admin
cross-log search + request-trace correlation:

  - timestamp / logged_at (timestamptz) → ISO STR (CONSUMED — router does
    fromisoformat via _parse_ts and stores on str fields)
  - status_code / response_time_ms (int) → native int
  - id (BIGINT) → native int (router str()s it)
  - extra->>request_id JSONB filter reproduced
  - TWO pre-existing BROKEN endpoints reproduced (NOT repaired): frontend_logs()
    (nonexistent column stack_trace) and audit_logs() (nonexistent table
    admin_audit_logs) both RAISE.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_search_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "/__test_orm_search_"


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
    finally:
        await conn.close()


def _repo():
    from app.repositories.admin.search_repository_orm import AdminSearchRepositoryOrm

    return AdminSearchRepositoryOrm()


async def test_request_logs_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    rid = uuid.uuid4().hex[:36]
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO api_request_logs "
            "(request_id, method, path, status_code, response_time_ms, timestamp) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            rid,
            "GET",
            f"{_PREFIX}{uuid.uuid4().hex[:6]}",
            503,
            999,
            now,
        )
    finally:
        await conn.close()

    rows = await _repo().request_logs(
        (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()
    )
    ours = [r for r in rows if r["path"].startswith(_PREFIX)]
    assert ours
    r = ours[0]
    assert type(r["timestamp"]) is str
    assert datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    assert type(r["status_code"]) is int and r["status_code"] == 503
    assert type(r["response_time_ms"]) is int and r["response_time_ms"] == 999
    assert type(r["id"]) is int
    assert set(r.keys()) == {
        "id",
        "request_id",
        "method",
        "path",
        "status_code",
        "response_time_ms",
        "timestamp",
        "error_detail",
    }


async def test_request_logs_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO api_request_logs (request_id, method, path, timestamp) "
            "VALUES ($1,'GET',$2,$3)",
            uuid.uuid4().hex[:36],
            f"{_PREFIX}in",
            now - timedelta(minutes=30),
        )
        await conn.execute(
            "INSERT INTO api_request_logs (request_id, method, path, timestamp) "
            "VALUES ($1,'GET',$2,$3)",
            uuid.uuid4().hex[:36],
            f"{_PREFIX}out",
            now - timedelta(hours=2),
        )
    finally:
        await conn.close()

    rows = await _repo().request_logs(
        (now - timedelta(hours=1)).isoformat(), now.isoformat()
    )
    paths = {r["path"] for r in rows}
    assert f"{_PREFIX}in" in paths
    assert f"{_PREFIX}out" not in paths


async def test_app_logs_by_request_id_jsonb_filter(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    target = uuid.uuid4().hex
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at, extra) "
            "VALUES ('INFO','m',$1,$2,$3::jsonb)",
            f"{_PREFIX}match",
            now,
            f'{{"request_id": "{target}"}}',
        )
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at, extra) "
            "VALUES ('INFO','m',$1,$2,$3::jsonb)",
            f"{_PREFIX}other",
            now,
            '{"request_id": "different"}',
        )
    finally:
        await conn.close()

    rows = await _repo().app_logs_by_request_id(target)
    msgs = {r["message"] for r in rows}
    assert f"{_PREFIX}match" in msgs
    assert f"{_PREFIX}other" not in msgs
    r = next(r for r in rows if r["message"] == f"{_PREFIX}match")
    assert type(r["logged_at"]) is str
    assert datetime.fromisoformat(r["logged_at"])


async def test_get_request_log_timestamp_is_iso(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    rid = uuid.uuid4().hex[:36]
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO api_request_logs (request_id, method, path, timestamp) "
            "VALUES ($1,'POST',$2,$3)",
            rid,
            f"{_PREFIX}{uuid.uuid4().hex[:6]}",
            now,
        )
    finally:
        await conn.close()

    log = await _repo().get_request_log(rid)
    assert log is not None
    assert type(log["timestamp"]) is str
    assert datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00"))
    # user_id is NULL here → None (the str() coercion only applies to non-null)
    assert log["user_id"] is None


async def test_get_request_log_absent_returns_none(integration_db_url, patched_engine):
    log = await _repo().get_request_log("no-such-request-id-" + uuid.uuid4().hex)
    assert log is None


# ─── BROKEN ENDPOINTS — reproduced faithfully (must RAISE, not repaired) ───


async def test_frontend_logs_broken_endpoint_raises(integration_db_url, patched_engine):
    """frontend_logs() selects the nonexistent column stack_trace → PG 42703.
    The ORM must reproduce the failure, NOT alias stack → stack_trace."""
    now = datetime.now(timezone.utc)
    with pytest.raises(Exception) as exc:
        await _repo().frontend_logs(
            (now - timedelta(hours=1)).isoformat(), now.isoformat()
        )
    # asyncpg UndefinedColumnError (42703) — surfaced through SQLAlchemy.
    assert (
        "stack_trace" in str(exc.value)
        or "42703" in str(exc.value)
        or ("does not exist" in str(exc.value))
    )


async def test_audit_logs_broken_endpoint_raises(integration_db_url, patched_engine):
    """audit_logs() queries the nonexistent table admin_audit_logs → PG 42P01.
    The ORM must reproduce the failure, NOT substitute the real audit_logs."""
    now = datetime.now(timezone.utc)
    with pytest.raises(Exception) as exc:
        await _repo().audit_logs(
            (now - timedelta(hours=1)).isoformat(), now.isoformat()
        )
    assert (
        "admin_audit_logs" in str(exc.value)
        or "42P01" in str(exc.value)
        or ("does not exist" in str(exc.value))
    )


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import search_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_SEARCH", False)
    assert type(mod.get_admin_search_repository()) is mod.AdminSearchRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import search_repository as mod
    from app.repositories.admin.search_repository_orm import AdminSearchRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_SEARCH", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_search_repository(), AdminSearchRepositoryOrm)
