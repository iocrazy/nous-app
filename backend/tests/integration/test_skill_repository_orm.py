"""Integration tests for SkillRepositoryOrm (Phase 2 M batch) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds on the skill surface (15 callsites):

  - skills.id (BIGINT) → native int (5.3 trap; every consumer int()s it).
  - skill_files.id (UUID) → str for shape parity (SkillFileOut.id: UUID accepts
    str; no consumer does UUID()/==/dict-key on it).
  - skills.created_by (uuid) → str; timestamps → ISO str; frontmatter_json
    (jsonb) → native dict; trigger_keywords (text[]) → native list.
  - upsert_file reproduces (skill_id, path) ON CONFLICT DO UPDATE.
  - upsert_file_versioned: new → INSERT v1; changed → snapshot + UPDATE v2;
    unchanged → no-op (returns current).
  - factory on/off.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_skill_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_NAME_PREFIX = "__test_orm_skill_"


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
async def cleanup(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # skill_files / skill_versions / skill_file_versions cascade off skills.
        await conn.execute("DELETE FROM skills WHERE name LIKE $1", _NAME_PREFIX + "%")
    finally:
        await conn.close()


def _repo():
    from app.repositories.skill_repository_orm import SkillRepositoryOrm

    return SkillRepositoryOrm()


def _name() -> str:
    return f"{_NAME_PREFIX}{uuid.uuid4().hex[:8]}"


def _slug() -> str:
    return f"test-orm-{uuid.uuid4().hex[:8]}"


# ─── insert / get / update (COMMIT + parity) ────────────────────────────


async def test_insert_commit_and_parity(integration_db_url, patched_engine, cleanup):
    name = _name()
    slug = _slug()
    created = await _repo().insert(
        {
            "name": name,
            "slug": slug,
            "is_public": True,
            "status": "active",
            "body_md": "# hi",
            "frontmatter_json": {"k": "v"},
        }
    )
    assert created is not None
    # bigint id → native int (5.3 trap).
    assert type(created["id"]) is int
    # jsonb → native dict.
    assert created["frontmatter_json"] == {"k": "v"}
    # timestamptz → ISO str.
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    # get_by_id + get_by_slug round-trip same parity.
    by_id = await _repo().get_by_id(created["id"])
    assert by_id is not None and type(by_id["id"]) is int
    by_slug = await _repo().get_by_slug(slug)
    assert by_slug is not None and by_slug["id"] == created["id"]

    # Confirm persisted.
    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM skills WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert cnt == 1


async def test_update_fields_and_base_update(patched_engine, cleanup):
    created = await _repo().insert(
        {"name": _name(), "slug": _slug(), "status": "active"}
    )
    # update_fields path.
    updated = await _repo().update_fields(created["id"], {"description": "desc"})
    assert updated["description"] == "desc"
    # BaseRepository.update override (used by skills_router + archive).
    updated2 = await _repo().update(str(created["id"]), {"icon": "🚀"})
    assert updated2["icon"] == "🚀"
    # archive() inherited → routes through the overridden update().
    await _repo().archive(created["id"])
    archived = await _repo().get_by_id(created["id"])
    assert archived["status"] == "archived"


async def test_list_skills_and_by_ids(patched_engine, cleanup):
    created = await _repo().insert(
        {"name": _name(), "slug": _slug(), "is_public": True, "status": "active"}
    )
    # list_skills returns the SUMMARY shape (no content_md key).
    skills = await _repo().list_skills()
    mine = [s for s in skills if s["id"] == created["id"]]
    assert len(mine) == 1
    assert "content_md" not in mine[0]  # summary projection
    assert type(mine[0]["id"]) is int

    batched = await _repo().list_by_ids([created["id"]])
    assert len(batched) == 1
    assert batched[0]["id"] == created["id"]


# ─── skill_files: upsert + UUID-str parity ──────────────────────────────


async def test_upsert_file_and_uuid_str_parity(patched_engine, cleanup):
    skill = await _repo().insert({"name": _name(), "slug": _slug(), "status": "active"})
    sid = skill["id"]

    f = await _repo().upsert_file(
        sid, path="SKILL.md", content="body", file_type="markdown"
    )
    # skill_files.id is UUID → str for shape parity.
    assert type(f["id"]) is str
    # It IS a parseable uuid string (proves we str()'d a real uuid).
    uuid.UUID(f["id"])
    # skill_id bigint → native int.
    assert type(f["skill_id"]) is int
    assert f["skill_id"] == sid
    assert f["content"] == "body"

    # Idempotent upsert on (skill_id, path) → DO UPDATE (no unique violation).
    f2 = await _repo().upsert_file(
        sid, path="SKILL.md", content="body2", file_type="markdown"
    )
    assert f2["content"] == "body2"
    assert f2["id"] == f["id"]  # same row updated

    # get_file + list_files same shape.
    got = await _repo().get_file(sid, "SKILL.md")
    assert got is not None and type(got["id"]) is str
    files = await _repo().list_files(sid)
    assert any(x["path"] == "SKILL.md" for x in files)
    assert all(type(x["id"]) is str for x in files)

    # delete_file.
    await _repo().delete_file(sid, "SKILL.md")
    assert await _repo().get_file(sid, "SKILL.md") is None


# ─── upsert_file_versioned (3 paths) ────────────────────────────────────


async def test_upsert_file_versioned_three_paths(
    integration_db_url, patched_engine, cleanup
):
    skill = await _repo().insert({"name": _name(), "slug": _slug(), "status": "active"})
    sid = skill["id"]

    # Path 1: new file → INSERT v1.
    v1 = await _repo().upsert_file_versioned(
        sid, path="ref.md", content="c1", file_type="markdown"
    )
    assert v1["current_version"] == 1
    assert type(v1["id"]) is str

    # Path 2: changed content → snapshot old + UPDATE → v2.
    v2 = await _repo().upsert_file_versioned(
        sid, path="ref.md", content="c2", file_type="markdown"
    )
    assert v2["current_version"] == 2
    assert v2["content"] == "c2"

    # A skill_file_versions snapshot row exists for v1.
    conn = await asyncpg.connect(integration_db_url)
    try:
        snap_cnt = await conn.fetchval(
            "SELECT count(*) FROM skill_file_versions sfv "
            "JOIN skill_files sf ON sf.id = sfv.skill_file_id "
            "WHERE sf.skill_id = $1 AND sf.path = 'ref.md'",
            sid,
        )
    finally:
        await conn.close()
    assert snap_cnt >= 1

    # Path 3: unchanged → no-op, returns current (version stays 2).
    v3 = await _repo().upsert_file_versioned(
        sid, path="ref.md", content="c2", file_type="markdown"
    )
    assert v3["current_version"] == 2


async def test_update_fields_versioned_snapshots(
    integration_db_url, patched_engine, cleanup
):
    skill = await _repo().insert(
        {"name": _name(), "slug": _slug(), "status": "active", "body_md": "v1"}
    )
    sid = skill["id"]
    # Tracked change (body_md) → snapshot + bump current_version.
    await _repo().update_fields_versioned(sid, {"body_md": "v2"}, notes="bump")
    after = await _repo().get_by_id(sid)
    assert after["body_md"] == "v2"
    assert after["current_version"] == 2

    conn = await asyncpg.connect(integration_db_url)
    try:
        snap = await conn.fetchval(
            "SELECT count(*) FROM skill_versions WHERE skill_id = $1", sid
        )
    finally:
        await conn.close()
    assert snap >= 1

    # No tracked change → no-op (no version bump).
    await _repo().update_fields_versioned(sid, {"description": "x only"})
    after2 = await _repo().get_by_id(sid)
    assert after2["current_version"] == 2


async def test_update_fields_versioned_missing_raises(patched_engine):
    with pytest.raises(ValueError):
        await _repo().update_fields_versioned(1, {"body_md": "x"})


# ─── delete (COMMIT) ────────────────────────────────────────────────────


async def test_delete_commit(integration_db_url, patched_engine, cleanup):
    skill = await _repo().insert({"name": _name(), "slug": _slug(), "status": "active"})
    sid = skill["id"]
    await _repo().delete(sid)
    assert await _repo().get_by_id(sid) is None


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.skill_repository import (
        SkillRepository,
        get_skill_repository,
    )

    with patch("app.core.config.settings.USE_ORM_SKILL", False):
        assert type(get_skill_repository()) is SkillRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.skill_repository_orm import SkillRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_SKILL", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.skill_repository import get_skill_repository

        assert type(get_skill_repository()) is SkillRepositoryOrm
