# backend/tests/integration/test_episode_progress_workflow_db.py
"""True-DB test for B4 Task 6's episodes/progress ``workflow`` and
``surface_state`` fields (``EpisodeRepository.progress_by_project``).

``tests/integration/test_episodes_progress_db.py`` seeds via
``_real_owner_and_team`` (SELECTs an existing ``auth.users``/``teams`` row) —
against the local ephemeral integration DB that has no pre-existing seed
data, both its tests SKIP (verified: `pytest.skip("No auth.users rows...")`
before ever reaching the assertions). This file instead follows Task 2's
``test_surface_criteria_db.py`` / Task 4's ``test_surface_completion_db.py``
self-seeding pattern (INSERT a throwaway ``auth.users`` + ``teams`` row per
test) so the new rollup columns get real DB coverage in this environment.

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" \\
        uv run pytest tests/integration/test_episode_progress_workflow_db.py -v -m integration
"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_b4prog_"


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
    """Delete test rows by name/identifier/email prefix, after each test.

    Deletion order respects FK constraints: ``issues`` (project_id SET NULL)
    and ``script_projects`` (RESTRICT FK to ``episodes``) go before
    ``projects`` (CASCADEs to ``episodes``/``project_stage_nodes``) and
    ``teams`` (CASCADEs to ``team_members``); ``auth.users`` goes last."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM issues WHERE identifier LIKE $1", _PREFIX + "%")
        await conn.execute(
            "DELETE FROM script_projects WHERE name LIKE $1", _PREFIX + "%"
        )
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM teams WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM auth.users WHERE email LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _seed_owner_and_team(conn):
    """Throwaway auth.users + teams row (mirrors test_surface_criteria_db.py
    / test_surface_completion_db.py — this DB has no pre-existing seed
    data)."""
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
        uuid.uuid4().hex[:16],
    )
    return owner_id, team_id


async def _seed_project_and_episode(conn, owner_id):
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
    return project_id, episode_id


async def _seed_node(
    conn, project_id, episode_id, *, name, sort_order, status="pending", skipped=False
):
    return await conn.fetchval(
        """
        INSERT INTO project_stage_nodes
            (project_id, episode_id, name, sort_order, parallel_group, status, skipped)
        VALUES ($1, $2, $3, $4, $4, $5, $6)
        RETURNING id
        """,
        project_id,
        episode_id,
        name,
        sort_order,
        status,
        skipped,
    )


async def _seed_issue(
    conn, project_id, node_id, owner_id, *, status, agent_outcome=None
):
    """A mirror issue for a stage node — origin_kind='project_stage',
    origin_id='project_stage:{project_id}:{node_id}' (the shape
    ``_needs_input_rollup_stmt`` matches via concat/cast)."""
    execution_state = (
        json.dumps({"agent_outcome": agent_outcome})
        if agent_outcome is not None
        else "{}"
    )
    return await conn.fetchval(
        """
        INSERT INTO issues
            (issue_number, identifier, title, status, priority,
             origin_kind, origin_id, project_id, created_by_user_id,
             execution_state)
        VALUES (
            (SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
            $1, $2, $3, 'medium', 'project_stage', $4, $5, $6, $7::jsonb
        )
        RETURNING id
        """,
        f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        f"{_PREFIX} mirror issue",
        status,
        f"project_stage:{project_id}:{node_id}",
        project_id,
        owner_id,
        execution_state,
    )


async def _seed_script_with_content(conn, project_id, team_id, episode_id, owner_id):
    """One non-deleted script -> one non-OMITTED scene with real content, so
    the script surface criterion reads True (storyboard stays False: no
    shots seeded)."""
    script_id = await conn.fetchval(
        """
        INSERT INTO script_projects
            (project_id, team_id, name, status, created_by, episode_id)
        VALUES ($1, $2, $3, 'active', $4, $5) RETURNING id
        """,
        project_id,
        team_id,
        f"{_PREFIX}script_{uuid.uuid4().hex[:8]}",
        owner_id,
        episode_id,
    )
    await conn.execute(
        "INSERT INTO script_scenes (script_id, content, content_json) "
        "VALUES ($1, $2, '[]'::jsonb)",
        script_id,
        "INT. 客厅 - 日",
    )


async def test_progress_by_project_carries_workflow_and_surface_state(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """3 real nodes (1 done, 2 not) + 1 needs_input mirror issue on the
    current node + 1 DONE (non-needs_input) issue on the done node (must
    NOT be counted) + real scene content (script True, storyboard False:
    no shots seeded). Matches the brief's Step-1 shape exactly."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        project_id, episode_id = await _seed_project_and_episode(conn, owner_id)

        node_done = await _seed_node(
            conn, project_id, episode_id, name="Script", sort_order=1, status="done"
        )
        node_cursor = await _seed_node(
            conn,
            project_id,
            episode_id,
            name="Storyboard",
            sort_order=2,
            status="in_progress",
        )
        await _seed_node(
            conn, project_id, episode_id, name="Render", sort_order=3, status="pending"
        )
        await conn.execute(
            "UPDATE episodes SET current_node_id=$1 WHERE id=$2",
            node_cursor,
            episode_id,
        )

        # needs_input mirror issue on the cursor node -> counted.
        await _seed_issue(
            conn,
            project_id,
            node_cursor,
            owner_id,
            status="needs_followup",
            agent_outcome="needs_input",
        )
        # a DONE mirror issue on the done node -> must NOT be counted
        # (wrong status, regardless of origin_id matching).
        await _seed_issue(conn, project_id, node_done, owner_id, status="done")

        await _seed_script_with_content(conn, project_id, team_id, episode_id, owner_id)
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    items = await get_episode_repository().progress_by_project(project_id)
    assert len(items) == 1
    item = items[0]

    assert item["episode_id"] == str(episode_id)
    assert item["workflow"] == {
        "nodes_total": 3,
        "nodes_done": 1,
        "current_node_id": str(node_cursor),
        "needs_input_count": 1,
    }
    assert item["surface_state"] == {"script": True, "storyboard": False}


async def test_progress_by_project_excludes_skipped_nodes_both_signals(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """nodes_total must exclude a node via EITHER skip signal
    (``skipped=true`` boolean OR ``status='skipped'`` string) — the two
    independent skip mechanisms project_stage_nodes carries."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, _team_id = await _seed_owner_and_team(conn)
        project_id, episode_id = await _seed_project_and_episode(conn, owner_id)

        await _seed_node(
            conn, project_id, episode_id, name="A", sort_order=1, status="pending"
        )
        await _seed_node(
            conn, project_id, episode_id, name="B", sort_order=2, status="pending"
        )
        await _seed_node(
            conn,
            project_id,
            episode_id,
            name="Skipped-bool",
            sort_order=3,
            status="pending",
            skipped=True,
        )
        await _seed_node(
            conn,
            project_id,
            episode_id,
            name="Skipped-status",
            sort_order=4,
            status="skipped",
        )
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    items = await get_episode_repository().progress_by_project(project_id)
    assert len(items) == 1
    assert items[0]["workflow"]["nodes_total"] == 2
    assert items[0]["workflow"]["nodes_done"] == 0
    assert items[0]["workflow"]["needs_input_count"] == 0


async def test_progress_by_project_zero_workflow_when_no_nodes(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """An episode with zero project_stage_nodes rows (absent from both
    rollup dicts, not an error) still reports all-zero workflow counts and
    a null cursor/surface_state, not a KeyError/500."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, _team_id = await _seed_owner_and_team(conn)
        project_id, _episode_id = await _seed_project_and_episode(conn, owner_id)
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    items = await get_episode_repository().progress_by_project(project_id)
    assert len(items) == 1
    assert items[0]["workflow"] == {
        "nodes_total": 0,
        "nodes_done": 0,
        "current_node_id": None,
        "needs_input_count": 0,
    }
    assert items[0]["surface_state"] == {"script": False, "storyboard": False}
