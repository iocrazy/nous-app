"""True-DB integration test for the episode progress aggregate (PR-10a,
spec G12, review fix 2).

The unit-level ``test_episode_progress.py`` exercises the pure
status-derivation matrix and the repository row-shaping against a
monkeypatched ``db_engine.fetch_all`` — it proves the mapping/shape but
never touches real SQL. This file proves the real JOIN + FILTER aggregate
(``EpisodeRepository.progress_by_project`` — episodes -> script_projects ->
script_scenes -> script_shots) actually resolves against the live schema,
and specifically that ``renders_count`` counts a shot with BOTH
``image_url`` and ``video_url`` (the image-then-video generation flow)
exactly ONCE — the bug the OR-FILTER fix (review fix 1) closes.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres" \\
        uv run pytest tests/integration/test_episodes_progress_db.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_epprogress_"


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

    ``script_projects.episode_id`` has ON DELETE RESTRICT back to
    ``episodes``, so ``script_projects`` must be deleted BEFORE
    ``projects`` — the project delete then cascades to ``episodes``
    (``episodes.project_id`` is ON DELETE CASCADE) with no script_projects
    row left to block it."""
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


async def _seed_project_with_episodes(conn):
    """Seed 1 project -> 2 episodes (Ep1 sort_order=1, Ep2 sort_order=2).

    Ep1 gets 1 script_project -> 1 script_scene -> 3 script_shots:
      - shot A: status='done', image_url only
      - shot B: status='done', BOTH image_url AND video_url (the
        image-then-video flow that used to double-count renders_count)
      - shot C: status='empty', no urls

    Ep2 gets no scripts at all (LEFT JOIN all-zero row).

    Returns (project_id, ep1_id, ep2_id).
    """
    owner_id, team_id = await _real_owner_and_team(conn)

    project_id = await conn.fetchval(
        """
        INSERT INTO projects (name, owner_id)
        VALUES ($1, $2) RETURNING id
        """,
        f"{_PREFIX}Test Project {uuid.uuid4().hex[:8]}",
        owner_id,
    )

    ep1_id = await conn.fetchval(
        """
        INSERT INTO episodes (project_id, title, sort_order)
        VALUES ($1, $2, 1) RETURNING id
        """,
        project_id,
        "Episode 1",
    )
    ep2_id = await conn.fetchval(
        """
        INSERT INTO episodes (project_id, title, sort_order)
        VALUES ($1, $2, 2) RETURNING id
        """,
        project_id,
        "Episode 2",
    )

    script_id = await conn.fetchval(
        """
        INSERT INTO script_projects
            (project_id, team_id, name, status, created_by, episode_id)
        VALUES ($1, $2, $3, 'active', $4, $5) RETURNING id
        """,
        project_id,
        team_id,
        f"{_PREFIX}Test Script {uuid.uuid4().hex[:8]}",
        owner_id,
        ep1_id,
    )

    scene_id = await conn.fetchval(
        """
        INSERT INTO script_scenes (script_id, content, content_json)
        VALUES ($1, '', '[]'::jsonb) RETURNING id
        """,
        script_id,
    )

    # Shot A: done, image_url only.
    await conn.execute(
        """
        INSERT INTO script_shots
            (scene_id, shot_number, status, image_url, sort_order)
        VALUES ($1, 1, 'done', $2, 1000)
        """,
        scene_id,
        "https://example.com/shot-a-image.png",
    )
    # Shot B: done, BOTH image_url and video_url — must count once toward
    # renders_count, not twice.
    await conn.execute(
        """
        INSERT INTO script_shots
            (scene_id, shot_number, status, image_url, video_url, sort_order)
        VALUES ($1, 2, 'done', $2, $3, 2000)
        """,
        scene_id,
        "https://example.com/shot-b-image.png",
        "https://example.com/shot-b-video.mp4",
    )
    # Shot C: empty, no urls at all.
    await conn.execute(
        """
        INSERT INTO script_shots (scene_id, shot_number, status, sort_order)
        VALUES ($1, 3, 'empty', 3000)
        """,
        scene_id,
    )

    return project_id, ep1_id, ep2_id


async def test_create_without_sort_order_increments_against_real_db(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """EpisodeRepository.create's COALESCE(MAX(sort_order)+1, 1) subquery
    (pre-ship review fix, PR-10b) resolves correctly against the live
    schema: two consecutive creates on the SAME project, neither passing an
    explicit sort_order, land at 1 then 2 — not both at the DB default of
    0, which used to make the first Move up/Move down PATCH swap a
    same-value 0<->0 no-op."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, _team_id = await _real_owner_and_team(conn)
        project_id = await conn.fetchval(
            """
            INSERT INTO projects (name, owner_id)
            VALUES ($1, $2) RETURNING id
            """,
            f"{_PREFIX}Sort Order Project {uuid.uuid4().hex[:8]}",
            owner_id,
        )
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    repo = get_episode_repository()
    ep1 = await repo.create({"project_id": project_id, "title": "Ep A"})
    ep2 = await repo.create({"project_id": project_id, "title": "Ep B"})

    assert ep1["sort_order"] == 1
    assert ep2["sort_order"] == 2


async def test_episodes_progress_real_join_counts_both_url_shot_once(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """EpisodeRepository.progress_by_project's raw-SQL JOIN (episodes ->
    script_projects -> script_scenes -> script_shots) resolves against the
    real schema, and renders_count uses the single OR-FILTER — a shot with
    both image_url and video_url counts ONCE, not twice."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        project_id, ep1_id, ep2_id = await _seed_project_with_episodes(conn)
    finally:
        await conn.close()

    from app.repositories.episode_repository import get_episode_repository

    items = await get_episode_repository().progress_by_project(project_id)
    by_id = {item["episode_id"]: item for item in items}

    ep1 = by_id[str(ep1_id)]
    assert ep1["scene_count"] == 1
    assert ep1["shots_total"] == 3
    assert ep1["shots_done"] == 2
    # 3 shots have a render-worthy URL between them (A: image, B: image+video
    # -> counted once, C: none) so renders_count must be 2, not 3.
    assert ep1["renders_count"] == 2
    assert ep1["status"] == "rendered"

    ep2 = by_id[str(ep2_id)]
    assert ep2["scene_count"] == 0
    assert ep2["shots_total"] == 0
    assert ep2["shots_done"] == 0
    assert ep2["renders_count"] == 0
    assert ep2["status"] == "planned"
