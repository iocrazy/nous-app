"""Integration tests for AuditLogsRepositoryOrm (Phase 2 admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds for the audit_logs admin trail:

  - id (uuid)         → STR
  - admin_id (uuid)   → STR (DICT KEY + AuditLogResponse.admin_id:str consumer)
  - created_at (tstz) → ISO STR (CONSUMED — /stats does created_at[:10])

Reads only (no writes in this repo). Date-range filters bind tz-aware datetimes.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_audit_logs_repository_orm.py -v
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_audit_"


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
        await conn.execute("DELETE FROM audit_logs WHERE action LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_admin_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy audit_logs.admin_id")
    return uid


async def _seed(conn, admin_id, **overrides) -> dict:
    defaults = {
        "admin_id": admin_id,
        "action": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        "target_type": "user",
        "target_id": uuid.uuid4().hex,
        "details": json.dumps({"k": "v"}),
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO audit_logs ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.admin.audit_logs_repository_orm import (
        AuditLogsRepositoryOrm,
    )

    return AuditLogsRepositoryOrm()


# ─── Reads + strategy-C parity ──────────────────────────────────────────


async def test_list_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin_id = await _real_admin_id(conn)
        a = await _seed(conn, admin_id)
        action = a["action"]
    finally:
        await conn.close()

    rows, total = await _repo().list(page=1, page_size=50, action=action)
    assert total == 1
    assert len(rows) == 1
    row = rows[0]
    # uuid id / admin_id → str.
    assert type(row["id"]) is str
    assert uuid.UUID(row["id"])  # valid uuid str
    assert type(row["admin_id"]) is str
    assert row["admin_id"] == str(admin_id)
    # timestamptz created_at → ISO str (CONSUMED by /stats created_at[:10]).
    assert type(row["created_at"]) is str
    assert datetime.fromisoformat(row["created_at"])
    assert row["created_at"][:10]  # slice-able exactly like the router does
    # details (jsonb) → native dict.
    assert row["details"] == {"k": "v"}


async def test_list_pagination_and_filters(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin_id = await _real_admin_id(conn)
        shared_action = f"{_PREFIX}shared_{uuid.uuid4().hex[:6]}"
        await _seed(conn, admin_id, action=shared_action, target_type="user")
        await _seed(conn, admin_id, action=shared_action, target_type="team")
        await _seed(conn, admin_id, action=shared_action, target_type="user")
    finally:
        await conn.close()

    rows, total = await _repo().list(page=1, page_size=2, action=shared_action)
    assert len(rows) == 2  # page capped
    assert total == 3  # exact count over all matches

    # target_type filter narrows.
    rows2, total2 = await _repo().list(
        page=1, page_size=50, action=shared_action, target_type="team"
    )
    assert total2 == 1
    assert rows2[0]["target_type"] == "team"


async def test_list_distinct_actions(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin_id = await _real_admin_id(conn)
        act_a = f"{_PREFIX}aaa_{uuid.uuid4().hex[:6]}"
        act_b = f"{_PREFIX}bbb_{uuid.uuid4().hex[:6]}"
        await _seed(conn, admin_id, action=act_a)
        await _seed(conn, admin_id, action=act_a)
        await _seed(conn, admin_id, action=act_b)
    finally:
        await conn.close()

    actions = await _repo().list_distinct_actions()
    ours = [a for a in actions if a in (act_a, act_b)]
    assert ours == sorted([act_a, act_b])  # sorted + de-duped


async def test_list_since_date_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_since binds a tz-aware datetime (v3) — pins the >= boundary."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin_id = await _real_admin_id(conn)
        after = await _seed(conn, admin_id, created_at=cutoff + timedelta(minutes=1))
        before = await _seed(conn, admin_id, created_at=cutoff - timedelta(minutes=1))
    finally:
        await conn.close()

    rows = await _repo().list_since(cutoff)
    ids = {r["id"] for r in rows}
    assert str(after["id"]) in ids  # at/after cutoff included
    assert str(before["id"]) not in ids  # before cutoff excluded


# ─── Flag-off / flag-on factory parity ──────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import audit_logs_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_AUDIT_LOGS", False)
    repo = mod.get_audit_logs_repository()
    assert type(repo) is mod.AuditLogsRepository
    from app.repositories.admin.audit_logs_repository_orm import (
        AuditLogsRepositoryOrm,
    )

    assert not isinstance(repo, AuditLogsRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import audit_logs_repository as mod
    from app.repositories.admin.audit_logs_repository_orm import (
        AuditLogsRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_AUDIT_LOGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_audit_logs_repository()
    assert isinstance(repo, AuditLogsRepositoryOrm)
