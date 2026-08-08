# backend/tests/integration/test_surface_criteria_db.py
"""True-DB test for EpisodeRepository.surface_criteria_for_episode (B4).

覆盖两点 SQL 口径(纯函数测不到的):OMITTED 场次被排除、空内容场次被排除。

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" \\
        uv run pytest tests/integration/test_surface_criteria_db.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_b4crit_"


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
    """Delete test rows by name/email prefix, after each test.

    Deletion order respects FK constraints: ``script_projects`` (NO ACTION
    FK to ``teams``, RESTRICT FK to ``episodes``) must go before both
    ``projects`` (CASCADEs to ``episodes``) and ``teams`` (CASCADEs to
    ``team_members``); ``auth.users`` goes last since ``projects.owner_id``
    / ``teams.owner_id`` reference it."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM script_projects WHERE name LIKE $1", _PREFIX + "%"
        )
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM teams WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM auth.users WHERE email LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _seed_owner_and_team(conn):
    """Throwaway auth.users + teams row. This DB has no pre-existing seed
    data (unlike test_episodes_progress_db.py's ``_real_owner_and_team``,
    which SELECTs an existing row), so we insert our own. ``teams.kind`` is
    omitted (defaults to 'collaborative') so the ``on_auth_user_created``
    trigger's auto-created 'personal' team for this owner doesn't collide
    with ``uq_teams_owner_personal``; ``teams_add_owner_trigger`` already
    inserts the owner into ``team_members`` for OUR team — no manual
    insert needed."""
    owner_id = await conn.fetchval(
        "INSERT INTO auth.users (id, email) VALUES (gen_random_uuid(), $1) "
        "RETURNING id",
        f"{_PREFIX}{uuid.uuid4().hex[:8]}@test.local",
    )
    team_id = await conn.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        f"{_PREFIX}team_{uuid.uuid4().hex[:8]}",
        owner_id,
        uuid.uuid4().hex[:16],  # teams.invite_code is varchar(20); prefix too long
    )
    return owner_id, team_id


async def _seed_episode_and_script(conn, owner_id, team_id, script_status="active"):
    """project -> episode -> script_project(episode_id bound). Returns
    (episode_id, script_id)."""
    project_id = await conn.fetchval(
        "INSERT INTO projects (name, owner_id) VALUES ($1, $2) RETURNING id",
        f"{_PREFIX}project_{uuid.uuid4().hex[:8]}",
        owner_id,
    )
    episode_id = await conn.fetchval(
        "INSERT INTO episodes (project_id, title, sort_order) "
        "VALUES ($1, $2, 1) RETURNING id",
        project_id,
        "Episode 1",
    )
    script_id = await conn.fetchval(
        """
        INSERT INTO script_projects
            (project_id, team_id, name, status, created_by, episode_id)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
        """,
        project_id,
        team_id,
        f"{_PREFIX}script_{uuid.uuid4().hex[:8]}",
        script_status,
        owner_id,
        episode_id,
    )
    return episode_id, script_id


async def _seed_scene(conn, script_id, content="", content_json="[]", omitted=False):
    omitted_at_sql = "now()" if omitted else "NULL"
    return await conn.fetchval(
        f"""
        INSERT INTO script_scenes (script_id, content, content_json, omitted_at)
        VALUES ($1, $2, $3::jsonb, {omitted_at_sql}) RETURNING id
        """,
        script_id,
        content,
        content_json,
    )


async def _seed_shot(conn, scene_id, shot_number, status="empty"):
    return await conn.fetchval(
        """
        INSERT INTO script_shots (scene_id, shot_number, status, sort_order)
        VALUES ($1, $2, $3, $2 * 1000) RETURNING id
        """,
        scene_id,
        shot_number,
        status,
    )


async def test_script_criterion_excludes_omitted_and_empty_scenes(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """scene A is OMITTED (has content but is excluded); scene B has empty
    content/content_json (not OMITTED but excluded). Neither counts toward
    scene_content_count, so script stays False. Adding scene C with real
    content flips it to True."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        episode_id, script_id = await _seed_episode_and_script(conn, owner_id, team_id)
        await _seed_scene(conn, script_id, content="x", omitted=True)  # scene A
        await _seed_scene(conn, script_id, content="", content_json="[]")  # scene B
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    repo = get_episode_repository()
    criteria = await repo.surface_criteria_for_episode(str(episode_id))
    assert criteria == {"script": False, "storyboard": False}

    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_scene(conn, script_id, content="INT. 客厅 - 日")  # scene C
    finally:
        await conn.close()

    criteria = await repo.surface_criteria_for_episode(str(episode_id))
    assert criteria["script"] is True


async def test_storyboard_criterion_all_done(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Two shots, one 'empty' one 'done' -> storyboard False. Flipping the
    remaining shot to 'done' -> storyboard True."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        episode_id, script_id = await _seed_episode_and_script(conn, owner_id, team_id)
        scene_id = await _seed_scene(conn, script_id, content="INT. 客厅 - 日")
        shot1_id = await _seed_shot(conn, scene_id, 1, status="empty")
        await _seed_shot(conn, scene_id, 2, status="done")
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    repo = get_episode_repository()
    criteria = await repo.surface_criteria_for_episode(str(episode_id))
    assert criteria["storyboard"] is False

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "UPDATE script_shots SET status = 'done' WHERE id = $1", shot1_id
        )
    finally:
        await conn.close()

    criteria = await repo.surface_criteria_for_episode(str(episode_id))
    assert criteria["storyboard"] is True


async def test_deleted_script_excluded(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """script.status='deleted' is filtered out of the query entirely, so
    even a fully-satisfied script/scene/shot chain underneath it counts
    for nothing."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        episode_id, script_id = await _seed_episode_and_script(
            conn, owner_id, team_id, script_status="deleted"
        )
        scene_id = await _seed_scene(conn, script_id, content="INT. 客厅 - 日")
        await _seed_shot(conn, scene_id, 1, status="done")
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    repo = get_episode_repository()
    criteria = await repo.surface_criteria_for_episode(str(episode_id))
    assert criteria == {"script": False, "storyboard": False}
