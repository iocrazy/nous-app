"""Integration tests for the Script*RepositoryOrm classes (Batch L1b) vs real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds across script_projects / script_chapters / script_assets /
script_storyboard_links:

  - ALL ids (id / script_id / project_id / team_id / chapter_id /
    storyboard_project_id) are bigint and STAY native int (the 5.3 trap).
  - script_projects.created_by (uuid) → STR (shape parity).
  - created_at / updated_at (timestamptz) → ISO STRING (the template rule).

Writes go through ``write_scope()`` (COMMITS) — fresh asyncpg reads confirm.

The script_projects FKs (project_id → projects, team_id → teams, created_by →
users) require real parent rows; the dev stack has none, so the fixture seeds a
throwaway team + project + storyboard_project and tears the whole chain down
(CASCADE handles chapters/assets/links).

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_script_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_script_"


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
async def scaffold(integration_db_url):
    """Seed a throwaway team + project + storyboard_project (+ owner user) so
    script_projects' FKs are satisfiable, and tear them all down after.

    Returns a dict with team_id / project_id / storyboard_project_id / user_id.
    Cleanup deletes script_projects for this project (CASCADE → chapters /
    assets / links), the storyboard_project, the project, and the team."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if not user_id:
            pytest.skip("No auth.users rows to satisfy created_by/owner FKs")
        team_id = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ($1, $2, $3) RETURNING id",
            f"{_PREFIX}team_{uuid.uuid4().hex[:8]}",
            user_id,
            uuid.uuid4().hex[:10],
        )
        project_id = await conn.fetchval(
            "INSERT INTO projects (name, owner_id, team_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            f"{_PREFIX}proj_{uuid.uuid4().hex[:8]}",
            user_id,
            team_id,
        )
        storyboard_project_id = await conn.fetchval(
            "INSERT INTO storyboard_projects (team_id, created_by, name) "
            "VALUES ($1, $2, $3) RETURNING id",
            team_id,
            user_id,
            f"{_PREFIX}sb_{uuid.uuid4().hex[:8]}",
        )
    finally:
        await conn.close()

    yield {
        "team_id": team_id,
        "project_id": project_id,
        "storyboard_project_id": storyboard_project_id,
        "user_id": user_id,
    }

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM script_projects WHERE project_id = $1", project_id
        )
        await conn.execute(
            "DELETE FROM storyboard_projects WHERE id = $1", storyboard_project_id
        )
        await conn.execute("DELETE FROM projects WHERE id = $1", project_id)
        await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
    finally:
        await conn.close()


async def _seed_project(conn, scaffold, **overrides) -> dict:
    defaults = {
        "project_id": scaffold["project_id"],
        "team_id": scaffold["team_id"],
        "created_by": scaffold["user_id"],
        "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO script_projects ({col_list}) VALUES ({placeholders}) "
        f"RETURNING *",
        *vals,
    )
    return dict(row)


def _project_repo():
    from app.repositories.script_repository_orm import ScriptProjectRepositoryOrm

    return ScriptProjectRepositoryOrm()


def _chapter_repo():
    from app.repositories.script_repository_orm import ScriptChapterRepositoryOrm

    return ScriptChapterRepositoryOrm()


def _asset_repo():
    from app.repositories.script_repository_orm import ScriptAssetRepositoryOrm

    return ScriptAssetRepositoryOrm()


def _link_repo():
    from app.repositories.script_repository_orm import (
        ScriptStoryboardLinkRepositoryOrm,
    )

    return ScriptStoryboardLinkRepositoryOrm()


# ─── script_projects ────────────────────────────────────────────────────


async def test_project_create_get_update_parity(
    integration_db_url, patched_engine, scaffold
):
    """create/get_by_id/update on script_projects: bigint ids stay int,
    created_by → str, timestamps → ISO, writes COMMIT."""
    repo = _project_repo()
    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    created = await repo.create(
        {
            "project_id": scaffold["project_id"],
            "team_id": scaffold["team_id"],
            "created_by": str(scaffold["user_id"]),
            "name": name,
        }
    )
    assert created["name"] == name
    # bigint ids stay int (the 5.3 trap).
    for col in ("id", "project_id", "team_id"):
        assert type(created[col]) is int
    # created_by uuid → str.
    assert type(created["created_by"]) is str
    # timestamps → ISO.
    for col in ("created_at", "updated_at"):
        assert type(created[col]) is str
        assert "T" in created[col] and " " not in created[col]

    # get_by_id round-trips (accepts str id like the service passes).
    fetched = await repo.get_by_id(str(created["id"]))
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert type(fetched["id"]) is int

    # update PERSISTS.
    updated = await repo.update(str(created["id"]), {"display_code": "S-001"})
    assert updated["display_code"] == "S-001"
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT display_code FROM script_projects WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == "S-001"

    # update on unknown id → {} (REST-contract parity).
    assert await repo.update(str(99999999999999999), {"name": "x"}) == {}


async def test_project_soft_delete_and_list_excludes(
    integration_db_url, patched_engine, scaffold
):
    """soft_delete sets status='deleted' (COMMIT); list_by_project excludes it
    and returns the {items,total,page,limit} envelope with int ids."""
    repo = _project_repo()
    conn = await asyncpg.connect(integration_db_url)
    try:
        keep = await _seed_project(conn, scaffold)
        gone = await _seed_project(conn, scaffold)
    finally:
        await conn.close()

    await repo.soft_delete(str(gone["id"]))
    conn = await asyncpg.connect(integration_db_url)
    try:
        status = await conn.fetchval(
            "SELECT status FROM script_projects WHERE id = $1", gone["id"]
        )
    finally:
        await conn.close()
    assert status == "deleted"

    page = await repo.list_by_project(project_id=scaffold["project_id"])
    assert set(page.keys()) == {"items", "total", "page", "limit"}
    ids = {item["id"] for item in page["items"]}
    assert keep["id"] in ids
    assert gone["id"] not in ids  # soft-deleted excluded
    assert all(type(item["id"]) is int for item in page["items"])

    # Search filter narrows by name (ilike).
    page2 = await repo.list_by_project(
        project_id=scaffold["project_id"], search=keep["name"]
    )
    assert keep["id"] in {item["id"] for item in page2["items"]}


# ─── script_chapters ────────────────────────────────────────────────────


async def test_chapter_crud_bulk_upsert_and_order(
    integration_db_url, patched_engine, scaffold
):
    """create / bulk_upsert (idempotent, ON CONFLICT id) / get_by_script
    (ordered) / hard_delete — bigint ids stay int, writes COMMIT."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        proj = await _seed_project(conn, scaffold)
    finally:
        await conn.close()

    repo = _chapter_repo()
    c1 = await repo.create({"script_id": proj["id"], "title": "ch1", "sort_order": 1})
    assert type(c1["id"]) is int
    assert type(c1["script_id"]) is int
    assert c1["script_id"] == proj["id"]

    # bulk_upsert: update the existing chapter + insert a new one.
    rows = await repo.bulk_upsert(
        str(proj["id"]),
        [
            {"id": c1["id"], "title": "ch1-edited", "sort_order": 1},
            {"title": "ch0", "sort_order": 0},
        ],
    )
    assert len(rows) == 2
    edited = next(r for r in rows if r["id"] == c1["id"])
    assert edited["title"] == "ch1-edited"

    # get_by_script ordered by sort_order (ch0 before ch1).
    chapters = await repo.get_by_script(str(proj["id"]))
    titles = [c["title"] for c in chapters]
    assert titles == ["ch0", "ch1-edited"]
    assert all(type(c["id"]) is int for c in chapters)

    # hard_delete removes a chapter (COMMIT).
    await repo.hard_delete(str(c1["id"]))
    conn = await asyncpg.connect(integration_db_url)
    try:
        still = await conn.fetchval(
            "SELECT count(*) FROM script_chapters WHERE id = $1", c1["id"]
        )
    finally:
        await conn.close()
    assert still == 0


# ─── script_assets ──────────────────────────────────────────────────────


async def test_asset_crud_and_type_filter(integration_db_url, patched_engine, scaffold):
    """create / list_by_script (+ asset_type filter) / hard_delete; int ids."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        proj = await _seed_project(conn, scaffold)
    finally:
        await conn.close()

    repo = _asset_repo()
    a1 = await repo.create(
        {
            "script_id": proj["id"],
            "asset_type": "character",
            "name": "hero",
            "sort_order": 0,
        }
    )
    a2 = await repo.create(
        {
            "script_id": proj["id"],
            "asset_type": "location",
            "name": "castle",
            "sort_order": 1,
        }
    )
    assert type(a1["id"]) is int
    assert type(a1["script_id"]) is int

    all_assets = await repo.list_by_script(str(proj["id"]))
    assert {a["id"] for a in all_assets} == {a1["id"], a2["id"]}

    chars = await repo.list_by_script(str(proj["id"]), asset_type="character")
    assert {a["id"] for a in chars} == {a1["id"]}

    await repo.hard_delete(str(a1["id"]))
    remaining = await repo.list_by_script(str(proj["id"]))
    assert {a["id"] for a in remaining} == {a2["id"]}


# ─── script_storyboard_links ────────────────────────────────────────────


async def test_link_crud_and_listings(integration_db_url, patched_engine, scaffold):
    """create / list_by_chapter / list_by_storyboard / hard_delete; int ids."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        proj = await _seed_project(conn, scaffold)
        chapter_id = await conn.fetchval(
            "INSERT INTO script_chapters (script_id, title) VALUES ($1, $2) "
            "RETURNING id",
            proj["id"],
            "ch-for-link",
        )
    finally:
        await conn.close()

    repo = _link_repo()
    link = await repo.create(
        {
            "chapter_id": chapter_id,
            "storyboard_project_id": scaffold["storyboard_project_id"],
        }
    )
    assert type(link["id"]) is int
    assert type(link["chapter_id"]) is int
    assert link["chapter_id"] == chapter_id

    by_chapter = await repo.list_by_chapter(str(chapter_id))
    assert {link_["id"] for link_ in by_chapter} == {link["id"]}

    by_sb = await repo.list_by_storyboard(str(scaffold["storyboard_project_id"]))
    assert link["id"] in {link_["id"] for link_ in by_sb}

    await repo.hard_delete(str(link["id"]))
    assert await repo.list_by_chapter(str(chapter_id)) == []


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import script_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_SCRIPTS", False)
    assert type(mod.get_script_project_repository()) is mod.ScriptProjectRepository
    assert type(mod.get_script_chapter_repository()) is mod.ScriptChapterRepository
    assert type(mod.get_script_asset_repository()) is mod.ScriptAssetRepository
    assert (
        type(mod.get_script_storyboard_link_repository())
        is mod.ScriptStoryboardLinkRepository
    )


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import script_repository as mod
    from app.repositories.script_repository_orm import (
        ScriptAssetRepositoryOrm,
        ScriptChapterRepositoryOrm,
        ScriptProjectRepositoryOrm,
        ScriptStoryboardLinkRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_SCRIPTS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_script_project_repository(), ScriptProjectRepositoryOrm)
    assert isinstance(mod.get_script_chapter_repository(), ScriptChapterRepositoryOrm)
    assert isinstance(mod.get_script_asset_repository(), ScriptAssetRepositoryOrm)
    assert isinstance(
        mod.get_script_storyboard_link_repository(),
        ScriptStoryboardLinkRepositoryOrm,
    )
