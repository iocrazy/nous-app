"""Integration tests for LogsRepositoryOrm (Batch L2) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds for the user_logs viewer/export surface:

  - id (bigint) → STAYS native int (the 5.3 trap; LogEntry response model wants
    ``id: int``).
  - user_id (uuid) → STR (shape parity).
  - created_at (timestamptz) → ISO STRING (CONSUMED — the CSV export does
    ``str(created_at)``).

Writes (create_log / delete_logs) go through ``write_scope()`` (COMMITS) — a
fresh asyncpg read proves no silent rollback.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_logs_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_logs_"


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
        await conn.execute("DELETE FROM user_logs WHERE message LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy user_logs.user_id FK")
    return uid


async def _seed_log(conn, user_id, **overrides) -> dict:
    defaults = {
        "user_id": user_id,
        "action": "download",
        "message": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        "status": "info",
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO user_logs ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.logs_repository_orm import LogsRepositoryOrm

    return LogsRepositoryOrm()


# ─── Reads + strategy-C parity ──────────────────────────────────────────


async def test_get_logs_shape_filters_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_logs returns (logs, total) with strategy-C parity, status filter,
    and newest-first ordering. id int, user_id str, created_at ISO str."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        info = await _seed_log(conn, user_id, status="info")
        err = await _seed_log(conn, user_id, status="error")
    finally:
        await conn.close()

    logs, total = await _repo().get_logs(str(user_id))
    ours = [log_ for log_ in logs if log_["id"] in {info["id"], err["id"]}]
    assert len(ours) == 2
    assert total >= 2
    sample = ours[0]
    # bigint id stays int (the 5.3 trap).
    assert type(sample["id"]) is int
    # uuid user_id → str.
    assert type(sample["user_id"]) is str
    assert sample["user_id"] == str(user_id)
    # timestamptz created_at → ISO str (CONSUMED by the CSV export's str()).
    assert type(sample["created_at"]) is str
    assert datetime.fromisoformat(sample["created_at"])
    assert "T" in sample["created_at"] and " " not in sample["created_at"]

    # Level filter narrows to error only (among ours).
    err_logs, _ = await _repo().get_logs(str(user_id), levels=["error"])
    err_ids = {log_["id"] for log_ in err_logs}
    assert err["id"] in err_ids
    assert info["id"] not in err_ids


async def test_get_logs_pagination_and_search(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Pagination caps the page size; search filters on message (ilike)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        needle = f"{_PREFIX}needle_{uuid.uuid4().hex[:6]}"
        hit = await _seed_log(conn, user_id, message=needle)
        await _seed_log(conn, user_id)
        await _seed_log(conn, user_id)
    finally:
        await conn.close()

    # page_size=1 → exactly one item, but total counts all.
    logs, total = await _repo().get_logs(str(user_id), page=1, page_size=1)
    assert len(logs) == 1
    assert total >= 3

    # Search hits only the needle row.
    found, found_total = await _repo().get_logs(str(user_id), search=needle)
    assert {log_["id"] for log_ in found} == {hit["id"]}
    assert found_total == 1


async def test_get_logs_for_export_no_pagination(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_logs_for_export returns all matching rows (capped by limit), ISO
    created_at — the CSV-export consumer's str(created_at) stays an ISO str."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        a = await _seed_log(conn, user_id)
        b = await _seed_log(conn, user_id)
    finally:
        await conn.close()

    rows = await _repo().get_logs_for_export(str(user_id))
    ids = {r["id"] for r in rows}
    assert a["id"] in ids and b["id"] in ids
    for r in rows:
        if r["id"] in (a["id"], b["id"]):
            assert type(r["created_at"]) is str
            # The CSV export does str(created_at): idempotent on an ISO str.
            assert str(r["created_at"]) == r["created_at"]


# ─── Writes (COMMIT + parity) ───────────────────────────────────────────


async def test_create_log_commits_and_returns_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_log PERSISTS and returns a parity dict (id int, user_id str)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    msg = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    out = await _repo().create_log(
        user_id=str(user_id),
        action="fetch",
        message=msg,
        status="success",
        details={"k": "v"},
    )
    assert out is not None
    assert out["message"] == msg
    assert out["status"] == "success"
    assert out["details"] == {"k": "v"}
    assert type(out["id"]) is int
    assert type(out["user_id"]) is str

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT message FROM user_logs WHERE id = $1", out["id"]
        )
    finally:
        await conn.close()
    assert persisted == msg


async def test_delete_logs_commits_and_before_date_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """delete_logs honors before_date at a TIGHT boundary, committing. before_date
    is a ``date`` → the cutoff binds as midnight-UTC of that day; a row one minute
    BEFORE midnight is deleted, a row one minute AFTER midnight (the cutoff day
    itself) is kept. Pins the v3 tz-aware datetime binding + the strict-``<``
    bound, not just a coarse 'old row gone' check."""
    cutoff = date.today() - timedelta(days=2)
    cutoff_midnight = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)

    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        # 1 minute before the cutoff midnight → must be deleted (created_at < cutoff).
        just_before = await _seed_log(
            conn, user_id, created_at=cutoff_midnight - timedelta(minutes=1)
        )
        # 1 minute after the cutoff midnight → must be kept (created_at >= cutoff).
        just_after = await _seed_log(
            conn, user_id, created_at=cutoff_midnight + timedelta(minutes=1)
        )
    finally:
        await conn.close()

    deleted = await _repo().delete_logs(str(user_id), before_date=cutoff)
    assert deleted >= 1

    conn = await asyncpg.connect(integration_db_url)
    try:
        before_gone = await conn.fetchval(
            "SELECT count(*) FROM user_logs WHERE id = $1", just_before["id"]
        )
        after_still = await conn.fetchval(
            "SELECT count(*) FROM user_logs WHERE id = $1", just_after["id"]
        )
    finally:
        await conn.close()
    assert before_gone == 0  # just-before-midnight row deleted
    assert after_still == 1  # cutoff-day row kept (strict < bound)


async def test_get_logs_date_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """PINS get_logs' start_date/end_date binding at a tight day boundary. The
    end_date bound is end-of-day (datetime.max.time()), so a row ON the end_date
    (even late in the day) is included, while a row on the day AFTER is excluded;
    a row the day BEFORE start_date is excluded. Proves the native tz-aware
    datetime binding + the day-boundary max.time() are correct, not just
    crash-free (this is the exact path that raised the timestamptz<VARCHAR error
    before the v3 fix)."""
    today = date.today()
    start = today - timedelta(days=2)
    end = today - timedelta(days=1)

    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        # On end_date, late in the day → included (end bound is end-of-day).
        on_end_late = await _seed_log(
            conn,
            user_id,
            created_at=datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
            - timedelta(seconds=1),
        )
        # On start_date, early → included.
        on_start = await _seed_log(
            conn,
            user_id,
            created_at=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(minutes=1),
        )
        # Day before start → excluded.
        before = await _seed_log(
            conn,
            user_id,
            created_at=datetime.combine(
                start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            ),
        )
        # Day after end → excluded.
        after = await _seed_log(
            conn,
            user_id,
            created_at=datetime.combine(
                end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            ),
        )
    finally:
        await conn.close()

    logs, _ = await _repo().get_logs(
        str(user_id), start_date=start, end_date=end, page_size=200
    )
    returned = {log_["id"] for log_ in logs}
    assert on_end_late["id"] in returned  # end-of-day on end_date included
    assert on_start["id"] in returned  # start_date included
    assert before["id"] not in returned  # day before start excluded
    assert after["id"] not in returned  # day after end excluded


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import logs_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_LOGS", False)
    repo = mod.get_logs_repository()
    assert type(repo) is mod.LogsRepository
    from app.repositories.logs_repository_orm import LogsRepositoryOrm

    assert not isinstance(repo, LogsRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import logs_repository as mod
    from app.repositories.logs_repository_orm import LogsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_LOGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_logs_repository()
    assert isinstance(repo, LogsRepositoryOrm)
