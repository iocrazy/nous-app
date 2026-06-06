"""Integration tests for ProjectsRepositoryOrm (Phase 2 L-solo) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds across the MediaTrack project surface (10 tables):

  - bigint ids + FKs (projects.id / project_files.id / project_folders.id /
    project_tasks.id / file_versions.id / shares.id / project_collections.id /
    project_id / media_id / folder_id / parent_id / file_id) → STAY native int
    (the 5.3 trap).
  - uuid columns (owner_id / uploaded_by / created_by / author_id /
    project_members.user_id / …) → STR (REST parity; CONSUMED by owner /
    membership / author compares).
  - timestamptz (created_at / updated_at / trashed_at / joined_at / …) → ISO STR.
  - date (project_tasks.due_date) → 'YYYY-MM-DD' STR.

Writes (create_* / update_* / delete_*) go through ``write_scope()`` (COMMITS) —
a fresh asyncpg read proves no silent rollback.

NOTE: there are NO date/timestamp RANGE filters in this repo (every query is
equality / IN / bool / ordering), so unlike LogsRepositoryOrm there is no
timestamptz<VARCHAR boundary to pin. The due_date round-trip below still
exercises the date → ISO-string parity path.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_projects_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_projects_"


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
    """Delete test projects (CASCADE drops files / folders / tasks / members /
    collections) by name prefix, after each test."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy projects.owner_id")
    return uid


async def _seed_project(conn, owner_id, **overrides) -> dict:
    defaults = {
        "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        "owner_id": owner_id,
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO projects ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.projects_repository_orm import ProjectsRepositoryOrm

    return ProjectsRepositoryOrm()


# ─── Reads + strategy-C parity ──────────────────────────────────────────


async def test_get_user_projects_shape_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_user_projects returns REST-shaped dicts: id int, owner_id str,
    created_at/updated_at ISO str, ordered by updated_at desc."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        p1 = await _seed_project(conn, user_id)
        p2 = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    projects = await _repo().get_user_projects(str(user_id))
    ours = [p for p in projects if p["id"] in {p1["id"], p2["id"]}]
    assert len(ours) == 2
    sample = ours[0]
    assert type(sample["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(sample["owner_id"]) is str  # uuid → str (CONSUMED by ownership)
    assert sample["owner_id"] == str(user_id)
    assert type(sample["created_at"]) is str
    assert "T" in sample["created_at"]  # ISO, not space-separated


async def test_get_project_by_id_and_personal_filter(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_project_by_id round-trips; team_id='personal' filters to NULL team."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        personal = await _seed_project(conn, user_id)  # team_id NULL
    finally:
        await conn.close()

    got = await _repo().get_project_by_id(str(personal["id"]))
    assert got is not None
    assert got["id"] == personal["id"]
    assert got["team_id"] is None

    personal_only = await _repo().get_user_projects(str(user_id), team_id="personal")
    assert personal["id"] in {p["id"] for p in personal_only}


# ─── Writes (COMMIT + parity) ───────────────────────────────────────────


async def test_create_and_update_project_commit(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_project + update_project PERSIST (write_scope commit) and return
    parity dicts; display_code (phantom column) is a graceful no-op."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    created = await _repo().create_project({"name": name, "owner_id": str(user_id)})
    assert created["name"] == name
    assert type(created["id"]) is int
    assert created["owner_id"] == str(user_id)

    # Persisted (no silent rollback).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM projects WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == name

    # update with a real column + a PHANTOM column (display_code) — phantom is
    # dropped (graceful no-op); the real column applies.
    updated = await _repo().update_project(
        str(created["id"]), {"description": "hello", "display_code": "P-XYZ"}
    )
    assert updated["description"] == "hello"
    assert "display_code" not in updated  # phantom column never materialized


async def test_update_project_all_phantom_keys_returns_current(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_project with ONLY a phantom column returns the current row
    unchanged (REST swallowed-PGRST parity), no exception."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    out = await _repo().update_project(str(proj["id"]), {"display_code": "P-1"})
    assert out["id"] == proj["id"]
    assert "display_code" not in out


async def test_file_crud_and_count_commit(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_file / get_project_files / get_project_file_count / update_file
    round-trip with parity (bigint ids int, uploaded_by str, timestamps ISO)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file(
        {
            "project_id": proj["id"],
            "filename": "clip.mp4",
            "file_type": "video",
            "uploaded_by": str(user_id),
        }
    )
    assert type(f["id"]) is int
    assert type(f["project_id"]) is int  # bigint FK stays int
    assert f["uploaded_by"] == str(user_id)
    assert type(f["created_at"]) is str and "T" in f["created_at"]

    count = await _repo().get_project_file_count(str(proj["id"]))
    assert count == 1

    files = await _repo().get_project_files(str(proj["id"]))
    assert {x["id"] for x in files} == {f["id"]}

    # Trash it → excluded from default count/list.
    await _repo().update_file(str(f["id"]), {"is_trashed": True})
    assert await _repo().get_project_file_count(str(proj["id"])) == 0


async def test_version_number_and_create(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_next_version_number increments off max; create_version commits."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file({"project_id": proj["id"], "filename": "a.mp4"})

    assert await _repo().get_next_version_number(str(f["id"])) == 1
    v1 = await _repo().create_version(
        {"file_id": f["id"], "version_number": 1, "filename": "a.mp4"}
    )
    assert v1["version_number"] == 1
    assert type(v1["id"]) is int
    assert await _repo().get_next_version_number(str(f["id"])) == 2


async def test_folder_crud_and_reparent(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Folder create/get/update + reparent_folder_children moves files/subfolders
    to the new parent. created_by uuid → str."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    parent = await _repo().create_folder(
        {"project_id": proj["id"], "name": "parent", "created_by": str(user_id)}
    )
    assert parent["created_by"] == str(user_id)
    child = await _repo().create_folder(
        {"project_id": proj["id"], "name": "child", "parent_id": parent["id"]}
    )
    f = await _repo().create_file(
        {"project_id": proj["id"], "filename": "x.mp4", "folder_id": parent["id"]}
    )

    # rename
    renamed = await _repo().update_folder(
        str(parent["id"]), str(proj["id"]), {"name": "renamed"}
    )
    assert renamed["name"] == "renamed"

    # reparent parent's children → None (root)
    await _repo().reparent_folder_children(str(parent["id"]), None)
    moved_file = await _repo().get_file_by_id(str(f["id"]))
    assert moved_file["folder_id"] is None
    moved_child = await _repo().get_folder(str(child["id"]), str(proj["id"]))
    assert moved_child["parent_id"] is None


async def test_task_crud_with_due_date_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_task / get_tasks / update_task — due_date (date) round-trips as a
    'YYYY-MM-DD' ISO string (strategy-C date parity)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    due = (date.today() + timedelta(days=3)).isoformat()
    t = await _repo().create_task(
        {
            "project_id": proj["id"],
            "title": "shoot",
            "created_by": str(user_id),
            "due_date": due,
            "status": "todo",
        }
    )
    assert type(t["id"]) is int
    assert t["created_by"] == str(user_id)
    # date → ISO 'YYYY-MM-DD' str (not a datetime.date object).
    assert type(t["due_date"]) is str
    assert t["due_date"] == due
    assert "T" not in t["due_date"]  # bare date, no time component

    tasks = await _repo().get_tasks(str(proj["id"]))
    assert {x["id"] for x in tasks} == {t["id"]}

    upd = await _repo().update_task(str(t["id"]), str(proj["id"]), {"status": "done"})
    assert upd["status"] == "done"

    assert await _repo().delete_task(str(t["id"]), str(proj["id"])) is True
    assert await _repo().get_tasks(str(proj["id"])) == []


async def test_member_create_and_list_composite_pk(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_member / get_members are genuine, non-drifted DB ops → overridden.
    Members use a composite PK (user_id + project_id); user_id uuid → str
    (CONSUMED by the enrich-email dict key)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    m = await _repo().create_member(
        {
            "project_id": proj["id"],
            "user_id": str(user_id),
            "role": "editor",
            "invited_by": str(user_id),
        }
    )
    assert m["user_id"] == str(user_id)  # uuid → str
    assert type(m["project_id"]) is int
    assert m["role"] == "editor"

    members = await _repo().get_members(str(proj["id"]))
    assert {x["user_id"] for x in members} == {str(user_id)}


def test_member_update_delete_are_inherited_not_overridden():
    """update_member / delete_member are NOT overridden — they INHERIT the
    legacy REST parent so flag-ON preserves the legacy phantom-``id`` SILENT
    NO-OP exactly as today (an inert parity migration must not repair the
    surface). Asserted at the override-presence level rather than by executing
    the inherited REST methods (which the integration harness can't reach).
    See the projects_repository_orm module docstring (MEMBER_ID deferred
    product decision)."""
    from app.repositories.projects_repository import ProjectsRepository
    from app.repositories.projects_repository_orm import ProjectsRepositoryOrm

    # NOT in the subclass __dict__ → inherited from the legacy parent.
    assert "update_member" not in ProjectsRepositoryOrm.__dict__
    assert "delete_member" not in ProjectsRepositoryOrm.__dict__
    assert ProjectsRepositoryOrm.update_member is ProjectsRepository.update_member
    assert ProjectsRepositoryOrm.delete_member is ProjectsRepository.delete_member
    # The genuine DB ops ARE overridden.
    assert "create_member" in ProjectsRepositoryOrm.__dict__
    assert "get_members" in ProjectsRepositoryOrm.__dict__


def test_comment_methods_are_inherited_not_overridden():
    """The four review_comments methods are NOT overridden — they INHERIT the
    legacy REST parent so flag-ON 500s identically to today (migration 062
    dropped the 043 schema the legacy methods target; a parity migration must
    reproduce that break, not repair it). See the projects_repository_orm module
    docstring (COMMENT deferred product decision)."""
    from app.repositories.projects_repository import ProjectsRepository
    from app.repositories.projects_repository_orm import ProjectsRepositoryOrm

    for name in (
        "get_comments_for_file",
        "create_comment",
        "get_comment_by_id",
        "delete_comment",
    ):
        assert name not in ProjectsRepositoryOrm.__dict__
        assert getattr(ProjectsRepositoryOrm, name) is getattr(ProjectsRepository, name)


async def test_share_and_collection_commit(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_share / get_shares_by_project + collection CRUD round-trip."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file({"project_id": proj["id"], "filename": "s.mp4"})
    share = await _repo().create_share(
        {
            "project_file_id": f["id"],
            "share_type": "link",
            "shared_by": str(user_id),
            "share_name": "demo",
            "share_code": uuid.uuid4().hex[:12],
            "status": "active",
        }
    )
    assert type(share["id"]) is int
    assert share["shared_by"] == str(user_id)

    shares = await _repo().get_shares_by_project(str(proj["id"]))
    assert share["id"] in {s["id"] for s in shares}

    col = await _repo().create_collection(
        {
            "project_id": proj["id"],
            "collection_code": uuid.uuid4().hex[:12],
            "collection_name": "drop",
            "created_by": str(user_id),
            "allowed_types": ["video"],
        }
    )
    assert type(col["id"]) is int
    assert col["allowed_types"] == ["video"]
    cols = await _repo().get_collections(str(proj["id"]))
    assert col["id"] in {c["id"] for c in cols}
    assert await _repo().delete_collection(str(col["id"]), str(proj["id"])) is True


async def test_review_status_update(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_review_status sets project_files.review_status, committing."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file({"project_id": proj["id"], "filename": "r.mp4"})
    out = await _repo().update_review_status(str(f["id"]), "approved")
    assert out["review_status"] == "approved"

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT review_status FROM project_files WHERE id = $1", f["id"]
        )
    finally:
        await conn.close()
    assert persisted == "approved"


# ─── Factory flag wiring ────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import projects_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_PROJECTS", False)
    repo = mod.get_projects_repository()
    assert type(repo) is mod.ProjectsRepository
    from app.repositories.projects_repository_orm import ProjectsRepositoryOrm

    assert not isinstance(repo, ProjectsRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import projects_repository as mod
    from app.repositories.projects_repository_orm import ProjectsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_PROJECTS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_projects_repository()
    assert isinstance(repo, ProjectsRepositoryOrm)
