"""Integration tests for ResourcesRepositoryOrm (Task 5.2) against real PG.

The resources repository (resources / resource_items / resource_versions /
folders) runs on the SQLAlchemy 2.0 ORM session layer now — ``read_scope()``
for reads, ``write_scope()`` (which COMMITS) for writes. These tests run real
SQL against a real Postgres to prove:

  - The migrated methods keep their exact dict return shapes (the swap must
    be invisible to call sites).
  - **THE P0 REGRESSION**: ``update_resource`` / ``create_version`` actually
    PERSIST. The old asyncpg path ran the write on a bare ``connect()`` (no
    txn) and silently rolled back on close, so a fresh read saw the OLD
    value. The ``*_persists_*`` tests open a SEPARATE fresh asyncpg
    connection and confirm the new value is there.
  - **CASCADE ATOMICITY**: ``trash_folder_cascade`` /
    ``restore_folder_cascade`` commit ALL rows of the cascade (folders +
    resources) in one transaction — verified via a fresh connection.
  - **TYPE PARITY**: the 3 resources ``Enum(AiTaskStatus)`` columns come
    back as bare ``str`` (not enum members); the embedded ``resource`` from
    get_resource_items is a ``dict`` (not a JSON string), and no value is a
    SQLAlchemy MetaData object.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema. Skips otherwise. Use the dev stack for these write-heavy tests:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_resources_repository_orm.py -v

Seed rows are tagged with a per-run prefix for predictable cleanup; the
fixture deletes them after each test even on failure.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import patch

import asyncpg
import pytest

from app.db.scope import Scope, request_scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_orm_res_"
_FILE_PREFIX = "__test_orm_res_file_"
_TEAM_PREFIX = "__test_orm_res_team_"


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN so
    read_scope()/write_scope() hit the test DB. Resets both singletons."""
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
    """Delete folders / resources / parsed_media tagged with the test
    prefixes (cascades resource_items / resource_versions via FKs)."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM folders WHERE name LIKE $1", _FILE_PREFIX + "%")
        await conn.execute(
            "DELETE FROM resources WHERE filename LIKE $1", _FILE_PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM resources WHERE media_id IN ("
            "SELECT id FROM parsed_media WHERE platform_id LIKE $1)",
            _PLATFORM_PREFIX + "%",
        )
        await conn.execute(
            "DELETE FROM parsed_media WHERE platform_id LIKE $1",
            _PLATFORM_PREFIX + "%",
        )
        await conn.execute("DELETE FROM teams WHERE name LIKE $1", _TEAM_PREFIX + "%")
    finally:
        await conn.close()


async def _real_ids(conn) -> tuple[str, int]:
    """A real (creator_id uuid, scope_id bigint=team id) pair to satisfy
    the resources / resource_items / folders FKs. Seeds a throwaway team
    (cleaned up by the fixture) since the dev DB may have none."""
    user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not user_id:
        pytest.skip("No auth.users rows to satisfy FKs")
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"{_TEAM_PREFIX}{uuid.uuid4().hex[:8]}",
        user_id,
        uuid.uuid4().hex[:10],
    )
    return user_id, team_id


@asynccontextmanager
async def _seed_parsed_media(
    integration_db_url: str, **overrides
) -> AsyncIterator[dict]:
    """Insert one parsed_media row and yield it as a dict."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        platform_id = f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:12]}"
        defaults = {
            "platform_id": platform_id,
            "source_platform": "douyin",
            "title": "ORM res test row",
            "original_url": f"https://test.example/{platform_id}",
        }
        defaults.update(overrides)
        cols = list(defaults.keys())
        vals = list(defaults.values())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        col_list = ", ".join(f'"{c}"' for c in cols)
        row = await conn.fetchrow(
            f"INSERT INTO parsed_media ({col_list}) VALUES ({placeholders}) "
            f"RETURNING *",
            *vals,
        )
        yield dict(row)
    finally:
        await conn.close()


def _repo():
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    return ResourcesRepositoryOrm()


# ─── Reads ─────────────────────────────────────────────────────────────


async def test_get_resource_by_id_str_input_and_type_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """resources.id is BIGINT; str path-param must coerce. AND: the 3
    Enum(AiTaskStatus) columns must surface as BARE str, never enum members
    (== passes for both; str()/f-string break on a leaked enum)."""
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3) RETURNING *",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}byid.mp4",
            )
        finally:
            await conn.close()

        result = await _repo().get_resource_by_id(str(resource["id"]))
        assert result is not None, "get_resource_by_id failed with str input"
        assert int(result["id"]) == resource["id"]
        assert result["filename"] == f"{_FILE_PREFIX}byid.mp4"

        # Enum read-parity for all three status columns.
        for col in ("transcript_status", "summary_status", "visual_analysis_status"):
            val = result[col]
            assert type(val) is str, (
                f"{col} leaked {type(val).__name__}, expected bare str — "
                f"enum-vs-string read parity drift"
            )
            # f-string parity — would yield 'AiTaskStatus.NONE' for a member.
            assert "AiTaskStatus" not in str(val)


async def test_get_resource_by_media_id_str_input(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3)",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}bymedia.mp4",
            )
        finally:
            await conn.close()

        result = await _repo().get_resource_by_media_id(str(media["id"]))
        assert result is not None
        assert int(result["media_id"]) == media["id"]


async def test_count_resources_by_media_id_returns_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            for i in range(3):
                await conn.execute(
                    "INSERT INTO resources (creator_id, media_id, source_type, "
                    "filename) VALUES ($1, $2, 'web', $3)",
                    user_id,
                    media["id"],
                    f"{_FILE_PREFIX}count_{i}.mp4",
                )
        finally:
            await conn.close()

        count = await _repo().count_resources_by_media_id(str(media["id"]))
        assert isinstance(count, int)
        assert count == 3


async def test_get_completed_resource_l2_dedup_shape(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """L2 dedup probe: nested {id, media_id, parsed_media: {...}} shape;
    parsed_media must be a dict and the statuses bare str."""
    test_url = f"https://test.example/{uuid.uuid4().hex}"
    async with _seed_parsed_media(
        integration_db_url,
        original_url=test_url,
        video_download_status="completed",
        media_type="0",
        platform_id=f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:12]}",
    ) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3)",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}l2.mp4",
            )
        finally:
            await conn.close()

        # A3: the read now routes through scoped_sql → requires an ambient scope
        # (it fail-closes with None when none is set). Acting user == creator_id.
        async with request_scope(Scope(user_id=str(user_id))):
            result = await _repo().get_completed_resource_by_url_and_creator(
                test_url, str(user_id)
            )
        assert result is not None, "L2 dedup probe failed to find seeded row"
        assert set(result.keys()) == {"id", "media_id", "parsed_media"}
        pm = result["parsed_media"]
        assert isinstance(pm, dict), f"parsed_media should be dict, got {type(pm)}"
        assert pm["original_url"] == test_url
        assert pm["video_download_status"] == "completed"
        assert type(pm["video_download_status"]) is str


async def test_get_owned_platform_ids_subset(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            await conn.execute(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename, file_path) VALUES ($1, $2, 'web', $3, '/x/y.mp4')",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}owned.mp4",
            )
        finally:
            await conn.close()

        # A3: routes through scoped_sql → requires an ambient scope.
        async with request_scope(Scope(user_id=str(user_id))):
            owned = await _repo().get_owned_platform_ids(
                [media["platform_id"], "__nope__"], str(user_id)
            )
        assert media["platform_id"] in owned
        assert "__nope__" not in owned


async def test_get_resource_items_nested_resource_is_dict(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_resource_items must decode row_to_json(r.*) to a dict (not leave
    it a JSON string), surface item created_at, and never carry updated_at
    (resource_items has no such column in the current schema)."""
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, team_id = await _real_ids(conn)
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3) RETURNING *",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}item.mp4",
            )
            await conn.execute(
                "INSERT INTO resource_items (resource_id, scope_id, added_by) "
                "VALUES ($1, $2, $3)",
                resource["id"],
                team_id,
                user_id,
            )
        finally:
            await conn.close()

        rows = await _repo().get_resource_items(str(team_id))
        match = next((r for r in rows if int(r["resource_id"]) == resource["id"]), None)
        assert match is not None, "seeded resource_item not returned"
        assert isinstance(match["resource"], dict), "nested resource must be a dict"
        assert int(match["resource"]["id"]) == resource["id"]
        assert "created_at" in match
        # resource_items has no updated_at column; must not leak the alias.
        assert "i_created_at" not in match
        assert "i_updated_at" not in match


# ─── Writes (committing — fixes the P0) ─────────────────────────────────


async def _fetch_filename_raw(integration_db_url: str, resource_id: int):
    conn = await asyncpg.connect(integration_db_url)
    try:
        return await conn.fetchval(
            "SELECT filename FROM resources WHERE id = $1", resource_id
        )
    finally:
        await conn.close()


async def test_create_resource_persists(
    integration_db_url, patched_engine, cleanup_test_rows
):
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
        finally:
            await conn.close()

        created = await _repo().create_resource(
            {
                "creator_id": str(user_id),
                "media_id": media["id"],
                "source_type": "web",
                "filename": f"{_FILE_PREFIX}created.mp4",
            }
        )
        assert created["filename"] == f"{_FILE_PREFIX}created.mp4"
        # Fresh independent read confirms the commit.
        name = await _fetch_filename_raw(integration_db_url, int(created["id"]))
        assert name == f"{_FILE_PREFIX}created.mp4"


async def test_update_resource_persists_after_commit_P0_REGRESSION(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """THE P0 REGRESSION TEST. The old asyncpg path ran the UPDATE on a bare
    connect() and silently rolled back on close. This updates the filename,
    opens a SEPARATE fresh connection, and asserts the new value persisted."""
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3) RETURNING *",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}before.mp4",
            )
        finally:
            await conn.close()

        new_name = f"{_FILE_PREFIX}{uuid.uuid4().hex[:8]}.mp4"
        updated = await _repo().update_resource(
            str(resource["id"]), {"filename": new_name}
        )
        assert updated["filename"] == new_name

        # Fresh, independent connection — proves the commit, not a buffer.
        persisted = await _fetch_filename_raw(integration_db_url, resource["id"])
        assert persisted == new_name, (
            "UPDATE did not persist — silent-rollback P0 is back. The write "
            "must go through write_scope() (which commits)."
        )

        # And a fresh repo read agrees.
        reread = await _repo().get_resource_by_id(str(resource["id"]))
        assert reread is not None
        assert reread["filename"] == new_name


async def test_create_version_persists_and_bigint_coerce(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create_version must persist (committing write_scope) AND coerce the
    snowflake-as-str resource_id / file_size_bytes to int8."""
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, _ = await _real_ids(conn)
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename) VALUES ($1, $2, 'web', $3) RETURNING *",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}ver.mp4",
            )
        finally:
            await conn.close()

        created = await _repo().create_version(
            {
                "resource_id": str(resource["id"]),  # snowflake-as-str
                "version_number": 1,
                "filename": f"{_FILE_PREFIX}ver.mp4",
                "file_size_bytes": "10485760",  # str → int8
            }
        )
        assert int(created["resource_id"]) == resource["id"]

        # Fresh read confirms the version committed.
        conn = await asyncpg.connect(integration_db_url)
        try:
            cnt = await conn.fetchval(
                "SELECT count(*) FROM resource_versions WHERE resource_id = $1",
                resource["id"],
            )
        finally:
            await conn.close()
        assert cnt == 1

        nxt = await _repo().get_next_version_number(str(resource["id"]))
        assert nxt == 2


# ─── Folder cascades (atomic — committing) ──────────────────────────────


async def _seed_folder_with_resource(integration_db_url, trashed_folder=False):
    """Create a folder, a resource, and a resource_item linking them.
    Returns (folder_id, resource_id, scope_id)."""
    async with _seed_parsed_media(integration_db_url) as media:
        conn = await asyncpg.connect(integration_db_url)
        try:
            user_id, team_id = await _real_ids(conn)
            folder = await conn.fetchrow(
                "INSERT INTO folders (name, scope_id, created_by, is_trashed) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                f"{_FILE_PREFIX}folder",
                team_id,
                user_id,
                trashed_folder,
            )
            resource = await conn.fetchrow(
                "INSERT INTO resources (creator_id, media_id, source_type, "
                "filename, is_trashed) VALUES ($1, $2, 'web', $3, $4) RETURNING id",
                user_id,
                media["id"],
                f"{_FILE_PREFIX}cascade.mp4",
                trashed_folder,
            )
            await conn.execute(
                "INSERT INTO resource_items (resource_id, scope_id, folder_id, "
                "added_by) VALUES ($1, $2, $3, $4)",
                resource["id"],
                team_id,
                folder["id"],
                user_id,
            )
            return folder["id"], resource["id"], team_id
        finally:
            await conn.close()


async def test_trash_folder_cascade_commits_all_rows(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """trash_folder_cascade must flip BOTH the folder and its resource to
    trashed, atomically and committed — verified via a fresh connection."""
    folder_id, resource_id, _ = await _seed_folder_with_resource(integration_db_url)

    result = await _repo().trash_folder_cascade(str(folder_id))
    assert result["trashed_folders"] >= 1
    assert result["trashed_resources"] >= 1

    # Fresh independent read — the cascade must be committed.
    conn = await asyncpg.connect(integration_db_url)
    try:
        f_trashed = await conn.fetchval(
            "SELECT is_trashed FROM folders WHERE id = $1", folder_id
        )
        r_trashed = await conn.fetchval(
            "SELECT is_trashed FROM resources WHERE id = $1", resource_id
        )
        r_last_folder = await conn.fetchval(
            "SELECT last_folder_id FROM resources WHERE id = $1", resource_id
        )
    finally:
        await conn.close()
    assert f_trashed is True, "folder trash did not commit"
    assert r_trashed is True, "resource trash did not commit (cascade not atomic)"
    # Restore snapshot captured for a later restore.
    assert int(r_last_folder) == folder_id


async def test_restore_folder_cascade_commits_all_rows(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """restore_folder_cascade must un-trash BOTH the folder and its resource,
    atomically and committed — verified via a fresh connection."""
    folder_id, resource_id, _ = await _seed_folder_with_resource(
        integration_db_url, trashed_folder=True
    )

    result = await _repo().restore_folder_cascade(str(folder_id))
    assert result["restored_folders"] >= 1
    assert result["restored_resources"] >= 1

    conn = await asyncpg.connect(integration_db_url)
    try:
        f_trashed = await conn.fetchval(
            "SELECT is_trashed FROM folders WHERE id = $1", folder_id
        )
        r_trashed = await conn.fetchval(
            "SELECT is_trashed FROM resources WHERE id = $1", resource_id
        )
    finally:
        await conn.close()
    assert f_trashed is False, "folder restore did not commit"
    assert r_trashed is False, "resource restore did not commit (cascade not atomic)"


# ─── Folders + versions read parity ─────────────────────────────────────


async def test_create_and_get_folder_roundtrip(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id, team_id = await _real_ids(conn)
    finally:
        await conn.close()

    created = await _repo().create_folder(
        {
            "name": f"{_FILE_PREFIX}folder",
            "scope_id": team_id,
            "created_by": str(user_id),
        }
    )
    assert created["name"] == f"{_FILE_PREFIX}folder"

    fetched = await _repo().get_folder_by_id(str(created["id"]))
    assert fetched is not None
    assert int(fetched["id"]) == int(created["id"])
    # smart_rules is a JSONB column — must be dict|None, never a str/MetaData.
    assert fetched["smart_rules"] is None or isinstance(fetched["smart_rules"], dict)

    folders = await _repo().get_folders(None, str(team_id))
    assert any(int(f["id"]) == int(created["id"]) for f in folders)
