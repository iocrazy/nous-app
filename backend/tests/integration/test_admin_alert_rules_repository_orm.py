"""Integration tests for AlertRulesRepositoryOrm (Phase 2 admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND strategy-C parity for the alert rules
+ alert history console (text()-backed — no ORM model exists for either table):

  - created_by (uuid) → STR; created_at / updated_at (timestamptz) → ISO STR
  - threshold (float8) → native float; id (BIGINT) → native int
  - WRITE round-trip (create → list → update → delete) COMMITS
  - list_history date-window boundary (tz-aware bind)

The alert_rules / alert_history tables (migration 094) may be absent in a given
integration DB — the module-level fixture SKIPS the whole file when they are not
present rather than failing.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_alert_rules_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_alert_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


async def _tables_exist(url: str) -> bool:
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('alert_rules','alert_history')"
        )
        return row == 2
    finally:
        await conn.close()


@pytest.fixture
async def require_tables(integration_db_url):
    if not await _tables_exist(integration_db_url):
        pytest.skip("alert_rules / alert_history not present in integration DB")


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
async def cleanup_test_rows(integration_db_url, require_tables):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM alert_history WHERE rule_name LIKE $1", _PREFIX + "%"
        )
        await conn.execute("DELETE FROM alert_rules WHERE name LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


def _repo():
    from app.repositories.admin.alert_rules_repository_orm import (
        AlertRulesRepositoryOrm,
    )

    return AlertRulesRepositoryOrm()


async def test_create_list_update_delete_round_trip(
    integration_db_url, patched_engine, cleanup_test_rows
):
    repo = _repo()
    created = await repo.create_rule(
        {
            "name": f"{_PREFIX}{uuid.uuid4().hex[:6]}",
            "metric_type": "error_rate",
            "condition": "gt",
            "threshold": 5.0,
            "window_minutes": 5,
            "notification_channel": "discord",
            "created_by": str(uuid.uuid4()),
        }
    )
    assert created is not None
    # Strategy-C parity on the returned row:
    assert type(created["id"]) is int
    assert type(created["threshold"]) is float and created["threshold"] == 5.0
    assert type(created["created_by"]) is str  # uuid → str
    assert type(created["created_at"]) is str  # timestamptz → ISO str
    assert datetime.fromisoformat(created["created_at"])
    rule_id = created["id"]

    rows, total = await repo.list_rules()
    ours = [r for r in rows if r["id"] == rule_id]
    assert ours and total >= 1
    assert type(ours[0]["id"]) is int

    updated = await repo.update_rule(rule_id, {"threshold": 9.0, "is_muted": True})
    assert updated is not None
    assert updated["threshold"] == 9.0 and updated["is_muted"] is True
    # update always stamps a fresh updated_at (legacy behaviour)
    assert type(updated["updated_at"]) is str

    await repo.delete_rule(rule_id)
    rows2, _ = await repo.list_rules()
    assert all(r["id"] != rule_id for r in rows2)


async def test_list_history_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    repo = _repo()
    created = await repo.create_rule(
        {
            "name": f"{_PREFIX}{uuid.uuid4().hex[:6]}",
            "metric_type": "error_count",
            "condition": "gt",
            "threshold": 1.0,
            "window_minutes": 5,
            "notification_channel": "discord",
        }
    )
    rule_id = created["id"]
    rule_name = f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        for created_at in (now - timedelta(minutes=10), now - timedelta(hours=5)):
            await conn.execute(
                "INSERT INTO alert_history (rule_id, rule_name, metric_type, "
                "metric_value, threshold, condition, message, created_at) "
                "VALUES ($1,$2,'error_count',2.0,1.0,'gt','msg',$3)",
                rule_id,
                rule_name,
                created_at,
            )
    finally:
        await conn.close()

    rows, total = await repo.list_history(
        page=1,
        page_size=50,
        rule_id=rule_id,
        start_date=now - timedelta(hours=1),
        end_date=now,
    )
    assert total == 1  # only the -10min row is inside [now-1h, now]
    assert rows[0]["rule_id"] == rule_id
    assert type(rows[0]["metric_value"]) is float
    assert type(rows[0]["created_at"]) is str


async def test_resolve_history_commits(
    integration_db_url, patched_engine, cleanup_test_rows
):
    repo = _repo()
    rule_name = f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        alert_id = await conn.fetchval(
            "INSERT INTO alert_history (rule_id, rule_name, metric_type, "
            "metric_value, threshold, condition, message) "
            "VALUES (NULL,$1,'error_count',2.0,1.0,'gt','msg') RETURNING id",
            rule_name,
        )
    finally:
        await conn.close()

    await repo.resolve_history(alert_id)

    conn = await asyncpg.connect(integration_db_url)
    try:
        resolved = await conn.fetchval(
            "SELECT resolved FROM alert_history WHERE id = $1", alert_id
        )
    finally:
        await conn.close()
    assert resolved is True


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import alert_rules_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_ALERT_RULES", False)
    assert type(mod.get_alert_rules_repository()) is mod.AlertRulesRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import alert_rules_repository as mod
    from app.repositories.admin.alert_rules_repository_orm import (
        AlertRulesRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_ALERT_RULES", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_alert_rules_repository(), AlertRulesRepositoryOrm)
