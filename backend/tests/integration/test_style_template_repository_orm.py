"""Integration tests for StyleTemplateRepositoryOrm (Batch L1) against real PG.

Proves the REST → ORM swap is invisible to call sites AND that STRATEGY-C
value-type parity holds for the style_templates table:

  - created_by (uuid) → STR at the dict boundary (REST-parity).
  - created_at / updated_at (timestamptz) → ISO STRING (the template rule).
  - id / team_id (bigint) → STAY native int (the 5.3 scope-zeroing trap).

Writes go through ``write_scope()`` (COMMITS) — a fresh asyncpg read proves no
silent rollback.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub schema.
Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_style_template_repository_orm.py -v

Seed rows are tagged with a per-run name prefix for predictable cleanup.
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
_PREFIX = "__test_orm_style_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN."""
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
            "DELETE FROM style_templates WHERE name LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy created_by FK")
    return uid


async def _seed(conn, **overrides) -> dict:
    defaults = {
        "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        "prompt_content": "do the thing",
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO style_templates ({col_list}) VALUES ({placeholders}) "
        f"RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.style_template_repository_orm import (
        StyleTemplateRepositoryOrm,
    )

    return StyleTemplateRepositoryOrm()


# ─── Reads ──────────────────────────────────────────────────────────────


async def test_get_by_id_shape_and_strategy_c_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_by_id returns a SELECT-* dict; created_by → str, timestamps → ISO
    str, bigint id stays int.

    NOTE: style_templates.team_id has a real FK to teams, and the dev stack has
    no teams rows — so seeds leave team_id NULL. The bigint-stays-int assertion
    is pinned on ``id`` (always present); team_id is exercised in the REST-shape
    key-set check only."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        seeded = await _seed(conn, created_by=user_id)
    finally:
        await conn.close()

    result = await _repo().get_by_id(str(seeded["id"]))
    assert result is not None
    for col in ("id", "name", "prompt_content", "is_public", "team_id", "created_by"):
        assert col in result, f"missing column {col} in SELECT * dict"
    # bigint id stays int (the 5.3 trap — never str a bigint).
    assert type(result["id"]) is int
    assert result["id"] == seeded["id"]
    # uuid → str.
    assert type(result["created_by"]) is str
    assert UUID(result["created_by"]) == seeded["created_by"]
    # timestamptz → ISO str.
    for col in ("created_at", "updated_at"):
        assert type(result[col]) is str
        assert datetime.fromisoformat(result[col])
        assert "T" in result[col] and " " not in result[col]

    # Unknown id → None.
    assert await _repo().get_by_id(str(99999999999999999)) is None


async def test_list_templates_filters_and_order(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_templates: public-only path, the team+public OR path (publics still
    surface), and the category filter.

    NOTE: style_templates.team_id FKs to teams, and the dev stack has no teams
    rows — so we can't seed team-scoped rows. We exercise the public side of the
    OR filter (a non-existent team_id is harmless; publics still match) plus the
    category filter and the public-only branch."""
    cat = "cat_" + uuid.uuid4().hex[:6]
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        pub_a = await _seed(conn, is_public=True, category=cat, created_by=user_id)
        pub_b = await _seed(conn, is_public=True, created_by=user_id)
        priv = await _seed(conn, is_public=False, created_by=user_id)
    finally:
        await conn.close()

    # team_id + include_public: the OR branch — publics still surface (the
    # team_id side matches nothing since the team doesn't exist).
    rows = await _repo().list_templates(team_id="7777", include_public=True)
    ids = {r["id"] for r in rows}
    assert pub_a["id"] in ids
    assert pub_b["id"] in ids
    assert priv["id"] not in ids  # not public, not the (nonexistent) team

    # category filter narrows to our tagged public row.
    rows_cat = await _repo().list_templates(team_id=None, category=cat)
    cat_ids = {r["id"] for r in rows_cat}
    assert pub_a["id"] in cat_ids
    assert pub_b["id"] not in cat_ids  # different category
    assert all(r["category"] == cat for r in rows_cat)

    # public-only branch when no team.
    rows_pub = await _repo().list_templates(team_id=None)
    pub_ids = {r["id"] for r in rows_pub}
    assert pub_a["id"] in pub_ids
    assert pub_b["id"] in pub_ids
    assert priv["id"] not in pub_ids


# ─── Writes (COMMIT + parity) ───────────────────────────────────────────


async def test_create_commits_and_returns_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create PERSISTS (write_scope commits) and returns a parity dict."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    out = await _repo().create(
        {"name": name, "prompt_content": "x", "created_by": str(user_id)}
    )
    assert out["name"] == name
    assert type(out["id"]) is int  # bigint stays int
    assert type(out["created_by"]) is str  # uuid → str

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM style_templates WHERE id = $1", out["id"]
        )
    finally:
        await conn.close()
    assert persisted == name


async def test_update_commits_and_no_match_returns_empty(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update PERSISTS; unknown id → {}."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = await _seed(conn, category="old")
    finally:
        await conn.close()

    out = await _repo().update(str(seeded["id"]), {"category": "new"})
    assert out["category"] == "new"
    assert type(out["id"]) is int

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT category FROM style_templates WHERE id = $1", seeded["id"]
        )
    finally:
        await conn.close()
    assert persisted == "new"

    assert await _repo().update(str(99999999999999999), {"category": "x"}) == {}


async def test_delete_commits(integration_db_url, patched_engine, cleanup_test_rows):
    """delete (→ hard_delete) removes the row and commits."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = await _seed(conn)
    finally:
        await conn.close()

    await _repo().delete(str(seeded["id"]))

    conn = await asyncpg.connect(integration_db_url)
    try:
        still = await conn.fetchval(
            "SELECT count(*) FROM style_templates WHERE id = $1", seeded["id"]
        )
    finally:
        await conn.close()
    assert still == 0


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    """USE_ORM_STYLE_TEMPLATES off (default) → legacy REST repo."""
    from app.core.config import settings
    from app.repositories import style_template_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_STYLE_TEMPLATES", False)
    repo = mod.get_style_template_repository()
    assert type(repo) is mod.StyleTemplateRepository
    from app.repositories.style_template_repository_orm import (
        StyleTemplateRepositoryOrm,
    )

    assert not isinstance(repo, StyleTemplateRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    """USE_ORM_STYLE_TEMPLATES on + engine configured → ORM subclass."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import style_template_repository as mod
    from app.repositories.style_template_repository_orm import (
        StyleTemplateRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_STYLE_TEMPLATES", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_style_template_repository()
    assert isinstance(repo, StyleTemplateRepositoryOrm)
