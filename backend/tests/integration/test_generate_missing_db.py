"""True-DB integration test for the batch generate-missing-frames fan-out
(PR-1 B3, Task 6).

The unit-level ``test_generate_missing.py`` exercises the fan-out loop
against a fake shots repo — it proves the loop shape (dispatch, rollback,
partial failure) but never touches real SQL. This file proves the two real
JOINs actually work against the live schema:

  - ``storyboard_progress_for_project`` — the
    script_projects → script_scenes → script_shots aggregation JOIN (raw SQL
    via ``db_engine.fetch_one``), including the FILTER-based status counts.
  - ``list_empty_shot_ids_for_project`` — the same JOIN shape filtered to
    ``status = 'empty'`` (the fan-out dispatch source), and that
    ``generate_missing_frames`` really flips those rows to ``'generating'``
    in the database (not just in a mock's call log).

DBOS dispatch itself is stubbed (``start_workflow_routed`` / task manager) —
this test is about the JOIN + the ``update_status`` write path, not the
worker.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres" \\
        uv run pytest tests/integration/test_generate_missing_db.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_genmiss_"


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
    """Delete test rows by name prefix, after each test.

    ``script_projects.project_id`` has NO cascade action (only
    ``script_scenes.script_id`` and ``script_shots.scene_id`` cascade), so
    ``script_projects`` must be deleted BEFORE ``projects`` or the FK blocks
    the project delete."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM script_projects WHERE name LIKE $1", _PREFIX + "%"
        )
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


async def _real_owner_and_team(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy projects.owner_id")
    team_id = await conn.fetchval("SELECT id FROM teams LIMIT 1")
    if not team_id:
        pytest.skip("No teams rows to satisfy script_projects.team_id")
    return uid, team_id


async def _seed_project_with_shots(conn, *, empty: int, done: int):
    """Seed 1 project → 1 script_project (status active) → 1 script_scene →
    (empty + done) script_shots. Returns (project_id, [empty_shot_id, ...])."""
    owner_id, team_id = await _real_owner_and_team(conn)

    project_id = await conn.fetchval(
        """
        INSERT INTO projects (name, owner_id)
        VALUES ($1, $2) RETURNING id
        """,
        f"{_PREFIX}Test Project {uuid.uuid4().hex[:8]}",
        owner_id,
    )

    script_id = await conn.fetchval(
        """
        INSERT INTO script_projects (project_id, team_id, name, status, created_by)
        VALUES ($1, $2, $3, 'active', $4) RETURNING id
        """,
        project_id,
        team_id,
        f"{_PREFIX}Test Script {uuid.uuid4().hex[:8]}",
        owner_id,
    )

    scene_id = await conn.fetchval(
        """
        INSERT INTO script_scenes (script_id, content, content_json)
        VALUES ($1, '', '[]'::jsonb) RETURNING id
        """,
        script_id,
    )

    empty_ids: list[str] = []
    for i in range(empty):
        shot_id = await conn.fetchval(
            """
            INSERT INTO script_shots (scene_id, shot_number, status, sort_order)
            VALUES ($1, $2, 'empty', $3) RETURNING id
            """,
            scene_id,
            i + 1,
            (i + 1) * 1000,
        )
        empty_ids.append(str(shot_id))

    for i in range(done):
        await conn.execute(
            """
            INSERT INTO script_shots (scene_id, shot_number, status, sort_order)
            VALUES ($1, $2, 'done', $3)
            """,
            scene_id,
            empty + i + 1,
            (empty + i + 1) * 1000,
        )

    return project_id, empty_ids


async def test_storyboard_progress_for_project_real_join(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """storyboard_progress_for_project's raw-SQL JOIN (script_projects →
    script_scenes → script_shots) resolves against the real schema and
    returns the correct aggregation."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        project_id, empty_ids = await _seed_project_with_shots(conn, empty=3, done=9)
    finally:
        await conn.close()

    from app.repositories.script_shot_repository import get_script_shot_repository

    progress = await get_script_shot_repository().storyboard_progress_for_project(
        project_id
    )
    assert progress == {
        "total": 12,
        "done": 9,
        "empty": 3,
        "generating": 0,
        "failed": 0,
        "script_count": 1,
        "scene_count": 1,
    }
    assert len(empty_ids) == 3


async def test_generate_missing_frames_real_dispatch_and_status_write(
    integration_db_url, patched_engine, cleanup_test_rows, monkeypatch
):
    """generate_missing_frames dispatches one workflow per empty shot and
    really writes status='generating' via update_status — proving both the
    list_empty_shot_ids_for_project JOIN and the write path against the live
    DB. DBOS dispatch (start_workflow_routed) and the task manager are
    stubbed so no worker is required."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        project_id, empty_ids = await _seed_project_with_shots(conn, empty=3, done=9)
    finally:
        await conn.close()

    dispatched: list[str] = []

    async def fake_start(name, **kw):
        dispatched.append(kw["dbos_workflow_kwargs"]["shot_id"])

    class _FakeTaskManager:
        def __init__(self):
            self._counter = 0

        async def create(self, **kw):
            self._counter += 1
            return f"fake-task-{self._counter}"

        async def fail(self, task_id, error_msg, **kw):
            pass

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager",
        lambda: _FakeTaskManager(),
    )
    monkeypatch.setattr(
        "app.services.library.projects_service.start_workflow_routed", fake_start
    )

    from app.services.library.projects_service import ProjectsService

    out = await ProjectsService().generate_missing_frames(project_id, "test-user")
    assert out["dispatched_count"] == 3
    assert sorted(dispatched) == sorted(empty_ids)

    from app.repositories.script_shot_repository import get_script_shot_repository

    shots_repo = get_script_shot_repository()
    remaining_empty = await shots_repo.list_empty_shot_ids_for_project(project_id)
    assert remaining_empty == []  # the 3 empty shots all left 'empty'

    for shot_id in empty_ids:
        row = await shots_repo.get_by_id(shot_id)
        assert row["status"] == "generating"
