"""Integration tests for LibrariesRepositoryOrm (Batch L1) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds for the libraries table:

  - created_by (uuid) → STR at the dict boundary (REST-parity).
  - created_at / updated_at (timestamptz) → ISO STRING (the template rule).
  - id (bigint) → STAYS native int (the 5.3 scope-zeroing trap).
  - scope_id / scope_type (text) → stay str.

REST-contract parity: create/update return {} when no row; delete returns True.
Writes commit via write_scope() — a fresh asyncpg read proves no rollback.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_libraries_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from uuid import UUID

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_lib_"


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
        await conn.execute("DELETE FROM libraries WHERE name LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy created_by FK")
    return uid


async def _seed(conn, **overrides) -> dict:
    """INSERT one libraries row. scope_type must be 'team' (CHECK constraint);
    created_by is NOT NULL with a real users FK, so default it to a real user."""
    defaults = {
        "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        "scope_type": "team",
        "scope_id": "team-" + uuid.uuid4().hex[:6],
        "created_by": await _real_user_id(conn),
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO libraries ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.libraries_repository_orm import LibrariesRepositoryOrm

    return LibrariesRepositoryOrm()


# ─── Reads ──────────────────────────────────────────────────────────────


async def test_get_by_id_shape_and_strategy_c_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_by_id returns a SELECT-* dict; created_by → str, timestamps → ISO,
    bigint id stays int, text scope_* stay str. Unknown id → None."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        seeded = await _seed(conn, created_by=user_id)
    finally:
        await conn.close()

    result = await _repo().get_by_id(str(seeded["id"]))
    assert result is not None
    for col in ("id", "name", "scope_type", "scope_id", "created_by", "visibility"):
        assert col in result, f"missing column {col} in SELECT * dict"
    assert type(result["id"]) is int  # bigint stays int (the 5.3 trap)
    assert type(result["created_by"]) is str  # uuid → str
    assert UUID(result["created_by"]) == seeded["created_by"]
    assert type(result["scope_id"]) is str
    assert type(result["scope_type"]) is str
    for col in ("created_at", "updated_at"):
        assert type(result[col]) is str
        assert datetime.fromisoformat(result[col])
        assert "T" in result[col] and " " not in result[col]

    assert await _repo().get_by_id(str(99999999999999999)) is None


async def test_list_by_scope_order(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_by_scope returns rows for the scope, ordered by sort_order ASC."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        scope_id = "scope-" + uuid.uuid4().hex[:6]
        b = await _seed(conn, scope_id=scope_id, sort_order=2, created_by=user_id)
        a = await _seed(conn, scope_id=scope_id, sort_order=1, created_by=user_id)
        # Different scope — must be excluded.
        await _seed(conn, scope_id="other-scope", created_by=user_id)
    finally:
        await conn.close()

    rows = await _repo().list_by_scope("team", scope_id)
    ours = [r for r in rows if r["id"] in {a["id"], b["id"]}]
    assert len(ours) == 2
    # Ordered by sort_order ASC: a (1) before b (2).
    assert [r["id"] for r in ours] == [a["id"], b["id"]]
    assert all(type(r["id"]) is int for r in ours)


# ─── Writes (COMMIT + parity) ───────────────────────────────────────────


async def test_create_commits_and_returns_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create PERSISTS and returns a parity dict (created_by str, id int)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    out = await _repo().create(
        {
            "name": name,
            "scope_type": "team",
            "scope_id": "team-xyz",
            "created_by": str(user_id),
        }
    )
    assert out["name"] == name
    assert type(out["id"]) is int
    assert type(out["created_by"]) is str

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM libraries WHERE id = $1", out["id"]
        )
    finally:
        await conn.close()
    assert persisted == name


async def test_update_commits_and_no_match_returns_empty(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update PERSISTS; unknown id → {} (REST-contract parity, no raise)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = await _seed(conn)
    finally:
        await conn.close()

    out = await _repo().update(str(seeded["id"]), {"name": _PREFIX + "renamed"})
    assert out["name"] == _PREFIX + "renamed"
    assert type(out["id"]) is int

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM libraries WHERE id = $1", seeded["id"]
        )
    finally:
        await conn.close()
    assert persisted == _PREFIX + "renamed"

    assert await _repo().update(str(99999999999999999), {"name": "x"}) == {}


async def test_delete_commits_returns_true(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """delete removes the row, commits, returns True."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = await _seed(conn)
    finally:
        await conn.close()

    assert await _repo().delete(str(seeded["id"])) is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        still = await conn.fetchval(
            "SELECT count(*) FROM libraries WHERE id = $1", seeded["id"]
        )
    finally:
        await conn.close()
    assert still == 0


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import libraries_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_LIBRARIES", False)
    repo = mod.get_libraries_repository()
    assert type(repo) is mod.LibrariesRepository
    from app.repositories.libraries_repository_orm import LibrariesRepositoryOrm

    assert not isinstance(repo, LibrariesRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import libraries_repository as mod
    from app.repositories.libraries_repository_orm import LibrariesRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_LIBRARIES", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_libraries_repository()
    assert isinstance(repo, LibrariesRepositoryOrm)
