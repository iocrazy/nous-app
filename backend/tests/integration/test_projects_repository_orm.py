"""Integration tests for the ORM-backed ProjectsRepository against real PG.

Proves STRATEGY-C value-type parity holds across the MediaTrack project surface
(9 tables — project_tasks was dropped in migration 176 and its repository
surface removed in PR-A3) after the post-rollout collapse to ORM-only:

  - bigint ids + FKs (projects.id / project_files.id / project_folders.id /
    file_versions.id / shares.id / project_collections.id /
    project_id / media_id / folder_id / parent_id / file_id) → STAY native int
    (the 5.3 trap).
  - uuid columns (owner_id / uploaded_by / created_by / author_id /
    project_members.user_id / …) → STR (REST parity; CONSUMED by owner /
    membership / author compares).
  - timestamptz (created_at / updated_at / trashed_at / joined_at / …) → ISO STR.
  - EXCEPTION: project_file_comments.id / file_id / version_id (PR-A2, Task 5)
    are stringified — this surface's wire contract (``CommentResponse`` /
    frontend ``ReviewComment``) expects string ids, unlike every other bigint
    id/FK above which stays native int.

Writes (create_* / update_* / delete_*) go through ``write_scope()`` (COMMITS) —
a fresh asyncpg read proves no silent rollback.

NOTE: there are NO date/timestamp RANGE filters in this repo (every query is
equality / IN / bool / ordering), so unlike LogsRepositoryOrm there is no
timestamptz<VARCHAR boundary to pin. The former date → ISO-string parity path
(project_tasks.due_date) was removed along with the table in PR-A3.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_projects_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

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
    from app.repositories.projects_repository import ProjectsRepository

    return ProjectsRepository()


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


async def test_member_update_and_delete_by_composite_pk_round_trip(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_member / delete_member identify the member by the composite PK
    ``(project_id, user_id)`` — ``member_id`` on the wire is the user_id. Proves
    the fix for the former phantom-``id`` SILENT NO-OP: the role change PERSISTS
    (write_scope commit) and the delete actually removes the row."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    await _repo().create_member(
        {
            "project_id": proj["id"],
            "user_id": str(user_id),
            "role": "viewer",
            "invited_by": str(user_id),
        }
    )

    # UPDATE by (project_id, user_id) → role change persists.
    upd = await _repo().update_member(str(user_id), str(proj["id"]), {"role": "editor"})
    assert upd is not None
    assert upd["user_id"] == str(user_id)
    assert type(upd["project_id"]) is int
    assert upd["role"] == "editor"
    # Fresh read confirms the commit (no silent rollback / no-op).
    members = await _repo().get_members(str(proj["id"]))
    assert {(m["user_id"], m["role"]) for m in members} == {(str(user_id), "editor")}

    # A non-existent user_id → no RETURNING row → None (service → 404).
    import uuid as _uuid

    missing = await _repo().update_member(
        str(_uuid.uuid4()), str(proj["id"]), {"role": "viewer"}
    )
    assert missing is None

    # DELETE by (project_id, user_id) → row actually removed.
    assert await _repo().delete_member(str(user_id), str(proj["id"])) is True
    assert await _repo().get_members(str(proj["id"])) == []


def test_member_update_delete_on_orm_path():
    """update_member / delete_member now run on the ORM path (composite-PK
    keyed), no longer the legacy supabase phantom-``id`` NO-OP. Verified by
    source: they use ``write_scope`` / ``read_scope`` and do NOT touch
    ``_get_client``; the four member DB ops are all ORM. See the MEMBER_ID note
    in the repository docstring."""
    import inspect

    from app.repositories.projects_repository import ProjectsRepository

    for name in ("update_member", "delete_member", "create_member", "get_members"):
        src = inspect.getsource(getattr(ProjectsRepository, name))
        assert "_get_client" not in src  # genuine ORM DB op
        assert "write_scope" in src or "read_scope" in src


def test_comment_methods_are_on_orm_path():
    """PR-A2 (Task 5): the four project_file_comments methods now run on the
    ORM path against the dedicated table (migration 335) — no longer the
    CONSCIOUS-KEEP legacy supabase path that 500'd on the dropped 043
    review_comments columns. Verified by source: they use write_scope /
    read_scope and never touch ``_get_client``."""
    import inspect

    from app.repositories.projects_repository import ProjectsRepository

    for name in (
        "get_comments_for_file",
        "create_comment",
        "get_comment_by_id",
        "delete_comment",
    ):
        src = inspect.getsource(getattr(ProjectsRepository, name))
        assert "_get_client" not in src  # genuine ORM DB op
        assert "write_scope" in src or "read_scope" in src


async def test_comment_crud_round_trip_on_project_file_comments(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_comment / get_comments_for_file / get_comment_by_id /
    delete_comment round-trip against project_file_comments (migration 335).
    id/file_id/version_id come back as STR (comment-surface wire contract —
    unlike every other bigint id/FK in this repo, which stays native int)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file({"project_id": proj["id"], "filename": "r.mp4"})
    v1 = await _repo().create_version(
        {"file_id": f["id"], "version_number": 1, "filename": "r.mp4"}
    )

    created = await _repo().create_comment(
        {
            "file_id": f["id"],
            "version_id": v1["id"],
            "author_id": str(user_id),
            "content": "Looks great",
            "timestamp_seconds": 3.5,
        }
    )
    assert type(created["id"]) is str
    assert created["file_id"] == str(f["id"])
    assert created["version_id"] == str(v1["id"])
    assert created["author_id"] == str(user_id)
    assert created["content"] == "Looks great"
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    listed = await _repo().get_comments_for_file(str(f["id"]))
    assert {c["id"] for c in listed} == {created["id"]}

    filtered = await _repo().get_comments_for_file(
        str(f["id"]), version_id=str(v1["id"])
    )
    assert {c["id"] for c in filtered} == {created["id"]}

    fetched = await _repo().get_comment_by_id(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]

    assert await _repo().delete_comment(created["id"]) is True
    assert await _repo().get_comment_by_id(created["id"]) is None
    assert await _repo().get_comments_for_file(str(f["id"])) == []


async def test_service_comment_round_trip_through_verify_gate(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """SERVICE-level round trip (add_comment → get_file_comments →
    delete_comment) with str path-param-shaped ids, exactly as the router
    calls it. This exercises ``_verify_file_in_project``, which used to
    compare the row's NATIVE-int project_id against the str path param and
    raised ``ValueError('File not found in this project')`` on EVERY call —
    killing all 8 file-scoped endpoints end-to-end. The repo-level round
    trip above cannot catch that; this one does."""
    from app.services.library.projects_service import ProjectsService

    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        proj = await _seed_project(conn, user_id)
    finally:
        await conn.close()

    f = await _repo().create_file({"project_id": proj["id"], "filename": "svc.mp4"})

    svc = ProjectsService()
    # str(...) everywhere — the router passes path params as strings.
    created = await svc.add_comment(
        project_id=str(proj["id"]),
        file_id=str(f["id"]),
        author_id=str(user_id),
        content="Through the gate",
        timestamp_seconds=1.25,
    )
    assert created["file_id"] == str(f["id"])
    assert created["author_id"] == str(user_id)

    listed = await svc.get_file_comments(str(proj["id"]), str(f["id"]))
    assert {c["id"] for c in listed} == {created["id"]}

    assert await svc.delete_comment(created["id"], str(user_id)) is True
    assert await svc.get_file_comments(str(proj["id"]), str(f["id"])) == []


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


# ─── Factory wiring (flag retired — ORM-only) ───────────────────────────


def test_factory_returns_projects_repository():
    """Post-collapse the factory is unconditional: it always returns a
    ``ProjectsRepository`` (the ORM bodies live directly on the class; the
    ``USE_ORM_PROJECTS`` flag and the ``ProjectsRepositoryOrm`` subclass are
    retired)."""
    from app.repositories import projects_repository as mod

    repo = mod.get_projects_repository()
    assert type(repo) is mod.ProjectsRepository
