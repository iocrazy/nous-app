"""Integration tests for the three admin log repos' ORM impls (Phase 2 admin wave).

RequestLogsRepositoryOrm / FrontendErrorLogsRepositoryOrm / AppLogsRepositoryOrm
over api_request_logs / frontend_error_logs / application_logs.

Proves REST → ORM swap invisibility + strategy-C parity:
  - timestamp / created_at / logged_at (timestamptz) → ISO STR
  - id (BIGINT) → native int; status_code / response_time_ms (int) → native int
  - FrontendErrorLogs renamed metadata_ keyed back as "metadata"
  - has_exception True/False filter (IS NOT NULL / IS NULL) parity
  - NOISE_MODULES exclusion; date-window boundary (tz-aware bind)

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_request_logs_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "/__test_orm_reqlog_"
_MSG_PREFIX = "__test_orm_applog_"
_FE_PREFIX = "__test_orm_felog_"


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
            "DELETE FROM application_logs WHERE message LIKE $1", _MSG_PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM frontend_error_logs WHERE error_type LIKE $1", _FE_PREFIX + "%"
        )
    finally:
        await conn.close()


def _req_repo():
    from app.repositories.admin.request_logs_repository_orm import (
        RequestLogsRepositoryOrm,
    )

    return RequestLogsRepositoryOrm()


def _fe_repo():
    from app.repositories.admin.request_logs_repository_orm import (
        FrontendErrorLogsRepositoryOrm,
    )

    return FrontendErrorLogsRepositoryOrm()


def _app_repo():
    from app.repositories.admin.request_logs_repository_orm import (
        AppLogsRepositoryOrm,
    )

    return AppLogsRepositoryOrm()


# ─── RequestLogs ────────────────────────────────────────────────────────


async def test_request_list_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    path = f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO api_request_logs "
            "(request_id, method, path, status_code, response_time_ms, timestamp) "
            "VALUES ($1,'POST',$2,201,55,$3)",
            uuid.uuid4().hex[:36],
            path,
            now,
        )
    finally:
        await conn.close()

    rows, total = await _req_repo().list_with_filters(page=1, page_size=50, path=path)
    assert total >= 1 and rows
    r = rows[0]
    assert type(r["id"]) is int
    assert type(r["timestamp"]) is str
    assert datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    assert type(r["status_code"]) is int and r["status_code"] == 201
    assert type(r["response_time_ms"]) is int


async def test_request_status_group_filter(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    base = f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        for sc, suffix in ((503, "5xx"), (200, "2xx")):
            await conn.execute(
                "INSERT INTO api_request_logs "
                "(request_id, method, path, status_code, timestamp) "
                "VALUES ($1,'GET',$2,$3,$4)",
                uuid.uuid4().hex[:36],
                f"{base}{suffix}",
                sc,
                now,
            )
    finally:
        await conn.close()

    rows, _ = await _req_repo().list_with_filters(
        page=1, page_size=50, path=base, status_group="5xx"
    )
    statuses = {r["status_code"] for r in rows}
    assert 503 in statuses and 200 not in statuses


# ─── FrontendErrorLogs (renamed metadata column) ────────────────────────


async def test_frontend_list_metadata_key_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    et = f"{_FE_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO frontend_error_logs (error_type, created_at, metadata) "
            "VALUES ($1,$2,$3::jsonb)",
            et,
            now,
            '{"k": "v"}',
        )
    finally:
        await conn.close()

    rows, total = await _fe_repo().list_with_filters(
        page=1, page_size=50, error_type=et
    )
    assert total >= 1 and rows
    r = rows[0]
    # The renamed attribute metadata_ must be keyed back as the DB column "metadata"
    assert "metadata" in r and "metadata_" not in r
    assert r["metadata"] == {"k": "v"}
    assert type(r["created_at"]) is str
    assert datetime.fromisoformat(r["created_at"])


# ─── AppLogs (has_exception + NOISE_MODULES) ────────────────────────────


async def test_app_has_exception_filter(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at, exception) "
            "VALUES ('ERROR','app.mod',$1,$2,'Traceback...')",
            f"{_MSG_PREFIX}withexc",
            now,
        )
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at) "
            "VALUES ('ERROR','app.mod',$1,$2)",
            f"{_MSG_PREFIX}noexc",
            now,
        )
    finally:
        await conn.close()

    rows_true, _ = await _app_repo().list_with_filters(
        page=1, page_size=100, message=_MSG_PREFIX, has_exception=True
    )
    msgs_true = {r["message"] for r in rows_true}
    assert f"{_MSG_PREFIX}withexc" in msgs_true
    assert f"{_MSG_PREFIX}noexc" not in msgs_true

    rows_false, _ = await _app_repo().list_with_filters(
        page=1, page_size=100, message=_MSG_PREFIX, has_exception=False
    )
    msgs_false = {r["message"] for r in rows_false}
    assert f"{_MSG_PREFIX}noexc" in msgs_false
    assert f"{_MSG_PREFIX}withexc" not in msgs_false


async def test_app_noise_module_excluded_by_default(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at) "
            "VALUES ('INFO','httpx',$1,$2)",
            f"{_MSG_PREFIX}noise",
            now,
        )
    finally:
        await conn.close()

    rows, _ = await _app_repo().list_with_filters(
        page=1, page_size=200, message=_MSG_PREFIX
    )
    msgs = {r["message"] for r in rows}
    # 'httpx' is in NOISE_MODULES → excluded when no explicit module filter is set
    assert f"{_MSG_PREFIX}noise" not in msgs
    # but reappears when the module is explicitly filtered
    rows2, _ = await _app_repo().list_with_filters(
        page=1, page_size=200, message=_MSG_PREFIX, module="httpx"
    )
    assert f"{_MSG_PREFIX}noise" in {r["message"] for r in rows2}


async def test_app_logged_at_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at) "
            "VALUES ('INFO','app.mod',$1,$2)",
            f"{_MSG_PREFIX}in",
            now - timedelta(minutes=10),
        )
        await conn.execute(
            "INSERT INTO application_logs (level, module, message, logged_at) "
            "VALUES ('INFO','app.mod',$1,$2)",
            f"{_MSG_PREFIX}out",
            now - timedelta(hours=5),
        )
    finally:
        await conn.close()

    rows, _ = await _app_repo().list_with_filters(
        page=1,
        page_size=200,
        message=_MSG_PREFIX,
        start_date=now - timedelta(hours=1),
        end_date=now,
    )
    msgs = {r["message"] for r in rows}
    assert f"{_MSG_PREFIX}in" in msgs
    assert f"{_MSG_PREFIX}out" not in msgs


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import request_logs_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_REQUEST_LOGS", False)
    assert type(mod.get_request_logs_repository()) is mod.RequestLogsRepository
    assert (
        type(mod.get_frontend_error_logs_repository())
        is mod.FrontendErrorLogsRepository
    )
    assert type(mod.get_app_logs_repository()) is mod.AppLogsRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import request_logs_repository as mod
    from app.repositories.admin.request_logs_repository_orm import (
        AppLogsRepositoryOrm,
        FrontendErrorLogsRepositoryOrm,
        RequestLogsRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_REQUEST_LOGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_request_logs_repository(), RequestLogsRepositoryOrm)
    assert isinstance(
        mod.get_frontend_error_logs_repository(), FrontendErrorLogsRepositoryOrm
    )
    assert isinstance(mod.get_app_logs_repository(), AppLogsRepositoryOrm)
