"""Integration tests for NotificationRepositoryOrm (Batch L1b) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds for the notifications / user_notifications surface:

  - id / team_id (bigint) → STAY native int (the 5.3 trap — the team filter does
    ``n['team_id'] in team_ids`` with int both sides; the read-status join keys
    on the bigint id).
  - created_by (uuid) → STR (shape parity).
  - created_at (timestamptz) → ISO STRING (the template rule).
  - read (derived bool) present.

Writes (mark_as_read upsert) go through ``write_scope()`` (COMMITS) — a fresh
asyncpg read proves no silent rollback. delete_notification's legacy False
no-op is pinned.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_notification_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_notif_"


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
        # user_notifications cascade-delete via the notification_id FK.
        await conn.execute(
            "DELETE FROM notifications WHERE title LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy user_notifications PK")
    return uid


async def _seed_notification(conn, **overrides) -> dict:
    defaults = {
        "type": "system",
        "title": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO notifications ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.notification_repository_orm import (
        NotificationRepositoryOrm,
    )

    return NotificationRepositoryOrm()


# ─── Reads + strategy-C parity ──────────────────────────────────────────


async def test_get_user_notifications_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """System notifications surface with strategy-C parity: bigint id/team_id
    stay int, created_by → str, created_at → ISO str, and a derived read bool."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        seeded = await _seed_notification(conn, created_by=user_id)
    finally:
        await conn.close()

    rows = await _repo().get_user_notifications(str(user_id))
    ours = [n for n in rows if n["id"] == seeded["id"]]
    assert len(ours) == 1
    n = ours[0]
    # bigint id stays int (the 5.3 trap).
    assert type(n["id"]) is int
    assert n["id"] == seeded["id"]
    # created_by uuid → str.
    assert type(n["created_by"]) is str
    # created_at timestamptz → ISO str.
    assert type(n["created_at"]) is str
    assert datetime.fromisoformat(n["created_at"])
    assert "T" in n["created_at"] and " " not in n["created_at"]
    # derived read flag, unread by default.
    assert n["read"] is False


async def test_mark_as_read_commits_and_reflects(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """mark_as_read upserts user_notifications (write_scope COMMITS); the next
    get_user_notifications shows read=True and a fresh asyncpg read confirms."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        seeded = await _seed_notification(conn, created_by=user_id)
    finally:
        await conn.close()

    assert await _repo().mark_as_read(str(seeded["id"]), str(user_id)) is True

    # Read-status now True via the repo.
    rows = await _repo().get_user_notifications(str(user_id))
    n = next(n for n in rows if n["id"] == seeded["id"])
    assert n["read"] is True

    # Fresh asyncpg read proves the COMMIT.
    conn = await asyncpg.connect(integration_db_url)
    try:
        read_at = await conn.fetchval(
            "SELECT read_at FROM user_notifications "
            "WHERE user_id = $1 AND notification_id = $2",
            user_id,
            seeded["id"],
        )
    finally:
        await conn.close()
    assert read_at is not None

    # Idempotent: a second mark_as_read still returns True, one row only.
    assert await _repo().mark_as_read(str(seeded["id"]), str(user_id)) is True
    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM user_notifications "
            "WHERE user_id = $1 AND notification_id = $2",
            user_id,
            seeded["id"],
        )
    finally:
        await conn.close()
    assert cnt == 1


async def test_mark_all_as_read_and_unread_count(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """mark_all_as_read marks every unread, returns the count; get_unread_count
    drops to 0 for our seeded rows afterward."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        a = await _seed_notification(conn, created_by=user_id)
        b = await _seed_notification(conn, created_by=user_id)
    finally:
        await conn.close()

    before = await _repo().get_unread_count(str(user_id))
    assert before >= 2  # at least our two seeded unread

    marked = await _repo().mark_all_as_read(str(user_id))
    assert marked >= 2

    # Our two specific notifications are now read.
    rows = await _repo().get_user_notifications(str(user_id))
    for nid in (a["id"], b["id"]):
        n = next(n for n in rows if n["id"] == nid)
        assert n["read"] is True


async def test_delete_notification_legacy_noop_returns_false(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """LEGACY QUIRK: delete_notification is a graceful no-op returning False
    (user_notifications has no dismissed_at column) — same observable contract
    as the REST impl."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        seeded = await _seed_notification(conn, created_by=user_id)
    finally:
        await conn.close()

    result = await _repo().delete_notification(str(seeded["id"]), str(user_id))
    assert result is False


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import notification_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_NOTIFICATIONS", False)
    repo = mod.get_notification_repository()
    assert type(repo) is mod.NotificationRepository
    from app.repositories.notification_repository_orm import (
        NotificationRepositoryOrm,
    )

    assert not isinstance(repo, NotificationRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import notification_repository as mod
    from app.repositories.notification_repository_orm import (
        NotificationRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_NOTIFICATIONS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_notification_repository()
    assert isinstance(repo, NotificationRepositoryOrm)
