"""Integration tests for UserLogsRepositoryOrm (Batch L2) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds for the append-only user_logs writer + read surface:

  - id (bigint) → STAYS native int (the 5.3 trap).
  - user_id (uuid) → STR (shape parity).
  - created_at (timestamptz) → ISO STRING (the template rule).

Plus the PRESERVED legacy guard: create() soft-skips (returns None) on a missing
user_id, BEFORE opening a session (the Celery orphan-download NOT-NULL spam
guard). Writes commit via ``write_scope()``.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_user_logs_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_ulogs_"


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
    from app.repositories.user_logs_repository_orm import UserLogsRepositoryOrm

    return UserLogsRepositoryOrm()


# ─── create + the preserved missing-user_id guard ───────────────────────


async def test_create_commits_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create PERSISTS and returns a parity dict (id int, user_id str,
    created_at ISO str)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    msg = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    out = await _repo().create(
        user_id=str(user_id), action="login", message=msg, status="success"
    )
    assert out is not None
    assert out["message"] == msg
    assert type(out["id"]) is int
    assert type(out["user_id"]) is str
    assert type(out["created_at"]) is str
    assert "T" in out["created_at"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT message FROM user_logs WHERE id = $1", out["id"]
        )
    finally:
        await conn.close()
    assert persisted == msg


async def test_create_skips_missing_user_id(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """PRESERVED LEGACY GUARD: create() returns None (writes nothing) when
    user_id is falsy or the literal string 'none'/'null' — the Celery
    orphan-download NOT-NULL spam guard. No session is even opened."""
    repo = _repo()
    for bad in ("", None, "none", "None", "NULL"):
        out = await repo.create(
            user_id=bad,  # type: ignore[arg-type]
            action="download",
            message=f"{_PREFIX}should_not_persist",
            status="info",
        )
        assert out is None

    # Nothing got written.
    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM user_logs WHERE message = $1",
            f"{_PREFIX}should_not_persist",
        )
    finally:
        await conn.close()
    assert cnt == 0


# ─── Reads ──────────────────────────────────────────────────────────────


async def test_get_recent_order_and_action_filter(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_recent: newest-first, limit honored, optional action filter; int id,
    str user_id, ISO created_at."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        await _seed_log(conn, user_id, action="download")
        login = await _seed_log(conn, user_id, action="login")
    finally:
        await conn.close()

    recent = await _repo().get_recent(str(user_id), limit=50)
    ours = [r for r in recent if r["message"].startswith(_PREFIX)]
    assert len(ours) >= 2
    assert type(ours[0]["id"]) is int
    assert type(ours[0]["user_id"]) is str
    assert "T" in ours[0]["created_at"]

    # action filter.
    logins = await _repo().get_recent(str(user_id), action="login")
    assert login["id"] in {r["id"] for r in logins}
    assert all(r["action"] == "login" for r in logins)


async def test_get_paginated_envelope(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_paginated returns the {logs,total,page,page_size,total_pages}
    envelope; level + search filters work; int/str/ISO parity in the rows."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        needle = f"{_PREFIX}pneedle_{uuid.uuid4().hex[:6]}"
        hit = await _seed_log(conn, user_id, message=needle, status="error")
        await _seed_log(conn, user_id, status="info")
    finally:
        await conn.close()

    env = await _repo().get_paginated(str(user_id), page=1, page_size=1)
    assert set(env.keys()) == {"logs", "total", "page", "page_size", "total_pages"}
    assert env["page_size"] == 1
    assert len(env["logs"]) <= 1
    assert env["total_pages"] >= 1

    # level + search narrows to the error needle row.
    env2 = await _repo().get_paginated(str(user_id), level="error", search=needle)
    assert {log_["id"] for log_ in env2["logs"]} == {hit["id"]}
    assert type(env2["logs"][0]["id"]) is int
    assert type(env2["logs"][0]["user_id"]) is str


async def test_get_paginated_date_window_boundary(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """PINS the v3 timestamptz-filter binding (start_date/end_date): seeds rows
    on BOTH sides of a tight [start, end] window (including ones a minute outside
    each edge) and asserts ONLY the in-window rows return. Proves the native
    tz-aware datetime binding works AND the boundary is inclusive/correct — not
    just that the query doesn't crash. ISO strings are passed exactly as the API
    layer hands them (the path that previously raised 'operator does not exist:
    timestamp with time zone < character varying')."""
    now = datetime.now(timezone.utc)
    win_start = now - timedelta(hours=2)
    win_end = now - timedelta(hours=1)

    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        # Inside the window (mid-window + 1 min inside each edge).
        in_mid = await _seed_log(
            conn, user_id, created_at=win_start + timedelta(minutes=30)
        )
        in_lo = await _seed_log(
            conn, user_id, created_at=win_start + timedelta(minutes=1)
        )
        in_hi = await _seed_log(
            conn, user_id, created_at=win_end - timedelta(minutes=1)
        )
        # Outside the window (1 min before start, 1 min after end).
        before = await _seed_log(
            conn, user_id, created_at=win_start - timedelta(minutes=1)
        )
        after = await _seed_log(
            conn, user_id, created_at=win_end + timedelta(minutes=1)
        )
    finally:
        await conn.close()

    # Pass ISO strings exactly like the API boundary does (start_date/end_date).
    env = await _repo().get_paginated(
        str(user_id),
        page=1,
        page_size=100,
        start_date=win_start.isoformat(),
        end_date=win_end.isoformat(),
    )
    returned = {log_["id"] for log_ in env["logs"]}
    assert {in_mid["id"], in_lo["id"], in_hi["id"]} <= returned  # all in-window
    assert before["id"] not in returned  # just before start excluded
    assert after["id"] not in returned  # just after end excluded


async def test_get_paginated_date_range_relative(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """PINS the date_range branch (the datetime.now(timezone.utc) base): a row
    inside the last 24h returns under date_range='24h'; a 25-hour-old row does
    not. Proves the relative-window lower-bound binding is correct."""
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        recent = await _seed_log(conn, user_id, created_at=now - timedelta(hours=2))
        stale = await _seed_log(conn, user_id, created_at=now - timedelta(hours=25))
    finally:
        await conn.close()

    env = await _repo().get_paginated(str(user_id), page_size=100, date_range="24h")
    returned = {log_["id"] for log_ in env["logs"]}
    assert recent["id"] in returned  # within 24h
    assert stale["id"] not in returned  # 25h old, excluded


async def test_get_by_aweme_id(integration_db_url, patched_engine, cleanup_test_rows):
    """get_by_aweme_id returns logs for a specific (user, aweme_id)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        aweme = f"aweme_{uuid.uuid4().hex[:8]}"
        hit = await _seed_log(conn, user_id, aweme_id=aweme)
        await _seed_log(conn, user_id, aweme_id="other_aweme")
    finally:
        await conn.close()

    rows = await _repo().get_by_aweme_id(str(user_id), aweme)
    assert {r["id"] for r in rows} == {hit["id"]}
    assert all(r["aweme_id"] == aweme for r in rows)


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import user_logs_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_USER_LOGS", False)
    repo = mod.get_user_logs_repository()
    assert type(repo) is mod.UserLogsRepository
    from app.repositories.user_logs_repository_orm import UserLogsRepositoryOrm

    assert not isinstance(repo, UserLogsRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import user_logs_repository as mod
    from app.repositories.user_logs_repository_orm import UserLogsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_USER_LOGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_user_logs_repository()
    assert isinstance(repo, UserLogsRepositoryOrm)
