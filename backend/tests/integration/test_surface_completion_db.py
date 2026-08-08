# backend/tests/integration/test_surface_completion_db.py
"""True-DB end-to-end test for B4 回流点接线 (surface_completion 挂在写路径上)。

覆盖 Task 4 的 8 个挂点如何在真实事务提交后驱动节点自动完成:mirror issue
存在时走 issue -> 投影钩子;mirror issue 全终态时 no-op;完全无镜像时走
set_node_status 直写。也回归 spec §5 ② 的场级联删除重算。

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" \\
        uv run pytest tests/integration/test_surface_completion_db.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_b4wire_"


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
    """Delete test rows by name/email/identifier prefix, after each test.

    Deletion order respects FK constraints: ``issues`` (project_id SET NULL,
    no hard dependency) can go anytime; ``script_projects`` (RESTRICT FK to
    ``episodes``) must go before ``projects`` (CASCADEs to
    ``episodes``/``project_stage_nodes``) and ``teams`` (CASCADEs to
    ``team_members``); ``auth.users`` goes last."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM issues WHERE identifier LIKE $1", _PREFIX + "%"
        )
        await conn.execute(
            "DELETE FROM script_projects WHERE name LIKE $1", _PREFIX + "%"
        )
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM teams WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute(
            "DELETE FROM auth.users WHERE email LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# Seed helpers
# --------------------------------------------------------------------------- #


async def _seed_owner_and_team(conn):
    """Throwaway auth.users + teams row (mirrors test_surface_criteria_db.py)."""
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


async def _seed_stage_nodes(
    conn, project_id, episode_id, *, script_review_required=False
):
    """Two surface nodes for the episode: Script (group 1, surface='script')
    and Storyboard (group 2, surface='storyboard'). episodes.current_node_id
    points at the Script node."""
    script_node_id = await conn.fetchval(
        """
        INSERT INTO project_stage_nodes
            (project_id, episode_id, name, sort_order, parallel_group,
             status, surface, review_required)
        VALUES ($1, $2, 'Script', 1, 1, 'in_progress', 'script', $3)
        RETURNING id
        """,
        project_id,
        episode_id,
        script_review_required,
    )
    storyboard_node_id = await conn.fetchval(
        """
        INSERT INTO project_stage_nodes
            (project_id, episode_id, name, sort_order, parallel_group,
             status, surface)
        VALUES ($1, $2, 'Storyboard', 2, 2, 'pending', 'storyboard')
        RETURNING id
        """,
        project_id,
        episode_id,
    )
    await conn.execute(
        "UPDATE episodes SET current_node_id=$1 WHERE id=$2",
        script_node_id,
        episode_id,
    )
    return script_node_id, storyboard_node_id


async def _seed_mirror_issue(conn, project_id, node_id, owner_id, *, status="in_progress"):
    """A mirror issue for a stage node — origin_kind='project_stage',
    origin_id='project_stage:{project_id}:{node_id}' — the shape
    ``_fire_stage_node_sync`` recognises."""
    issue_id = await conn.fetchval(
        """
        INSERT INTO issues
            (issue_number, identifier, title, status, priority,
             origin_kind, origin_id, project_id, created_by_user_id)
        VALUES (
            (SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
            $1, $2, $3, 'medium', 'project_stage', $4, $5, $6
        )
        RETURNING id
        """,
        f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        f"{_PREFIX} mirror issue",
        status,
        f"project_stage:{project_id}:{node_id}",
        project_id,
        owner_id,
    )
    return issue_id


async def _seed_common(conn, *, script_review_required=False):
    """project + episode + 2 surface nodes + Script node's mirror issue
    (in_progress). Returns a dict of every id a test might need."""
    owner_id, team_id = await _seed_owner_and_team(conn)
    project_id, episode_id = await _seed_project_and_episode(conn, owner_id)
    script_node_id, storyboard_node_id = await _seed_stage_nodes(
        conn, project_id, episode_id, script_review_required=script_review_required
    )
    issue_id = await _seed_mirror_issue(conn, project_id, script_node_id, owner_id)
    return {
        "owner_id": owner_id,
        "team_id": team_id,
        "project_id": project_id,
        "episode_id": episode_id,
        "script_node_id": script_node_id,
        "storyboard_node_id": storyboard_node_id,
        "issue_id": issue_id,
    }


async def _node_status(conn, node_id) -> str:
    return await conn.fetchval(
        "SELECT status FROM project_stage_nodes WHERE id=$1", node_id
    )


async def _issue_status(conn, issue_id) -> str:
    return await conn.fetchval("SELECT status FROM issues WHERE id=$1", issue_id)


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


async def test_scene_content_completes_script_node(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Writing real scene content flips the script criterion true; the Script
    node has an open mirror issue, so completion goes through
    transition_status -> _fire_stage_node_sync (issue-first path)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    await scene_repo.create_with_content(
        {"script_id": script["id"]},
        [{"id": "el_1", "type": "action", "text": "INT. 客厅 - 日 一家人围坐。"}],
        actor=str(seed["owner_id"]),
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["script_node_id"]) == "done"
        assert await _issue_status(conn, seed["issue_id"]) == "done"
    finally:
        await conn.close()


async def test_all_shots_done_completes_storyboard_node(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Storyboard node has NO mirror issue -> direct set_node_status write
    path. Flips true only once every shot in the (single) script is done."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    scene = await scene_repo.create({"script_id": script["id"]})

    shot_repo = get_script_shot_repository()
    shot1 = await shot_repo.create({"scene_id": scene["id"], "shot_number": 1})
    shot2 = await shot_repo.create({"scene_id": scene["id"], "shot_number": 2})

    await shot_repo.update_status(shot1["id"], "done")

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["storyboard_node_id"]) != "done"
    finally:
        await conn.close()

    await shot_repo.update_status(shot2["id"], "done")

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["storyboard_node_id"]) == "done"
    finally:
        await conn.close()


async def test_scene_delete_cascades_and_recomputes_storyboard(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """spec §5 ② 回归: scene A's shots are all done; scene B holds an empty
    shot that keeps storyboard False. Hard-deleting scene B (numbering
    unlocked) cascades its shots away — the criterion recomputes true."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    shot_repo = get_script_shot_repository()

    scene_a = await scene_repo.create({"script_id": script["id"]})
    shot_a1 = await shot_repo.create({"scene_id": scene_a["id"], "shot_number": 1})

    scene_b = await scene_repo.create({"script_id": script["id"]})
    await shot_repo.create({"scene_id": scene_b["id"], "shot_number": 1})  # stays empty

    # Mark shot A done only AFTER both scenes/shots exist, so the criterion
    # check (shots_total=2, shots_done=1) correctly stays False here.
    await shot_repo.update_status(shot_a1["id"], "done")

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["storyboard_node_id"]) != "done"
    finally:
        await conn.close()

    result = await scene_repo.delete(scene_b["id"])
    assert result["deleted"] is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["storyboard_node_id"]) == "done"
        remaining = await conn.fetchval(
            "SELECT count(*) FROM script_shots WHERE scene_id=$1", scene_b["id"]
        )
        assert remaining == 0
    finally:
        await conn.close()


async def test_omitted_scene_does_not_satisfy_script(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """The only scene is OMITTED — the criteria SQL excludes it regardless of
    content, so writing content via apply_element_ops must not complete the
    Script node."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    scene = await scene_repo.create({"script_id": script["id"]})

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "UPDATE script_scenes SET omitted_at = now() WHERE id = $1", scene["id"]
        )
    finally:
        await conn.close()

    await scene_repo.apply_element_ops(
        scene["id"],
        [{"op": "insert", "element_id": "el_1", "payload": {"type": "action", "text": "Hello."}}],
        expected_version=0,
        actor=str(seed["owner_id"]),
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["script_node_id"]) == "in_progress"
        assert await _issue_status(conn, seed["issue_id"]) == "in_progress"
    finally:
        await conn.close()


async def test_review_required_surface_node_not_auto_completed(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Script node has review_required=True: the criterion goes true, but
    should_auto_complete keeps the human review lane — no auto-complete."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn, script_review_required=True)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    await scene_repo.create_with_content(
        {"script_id": script["id"]},
        [{"id": "el_1", "type": "action", "text": "INT. 客厅 - 日 一家人围坐。"}],
        actor=str(seed["owner_id"]),
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["script_node_id"]) == "in_progress"
        assert await _issue_status(conn, seed["issue_id"]) == "in_progress"
    finally:
        await conn.close()


async def test_product_delete_does_not_revert(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Once the Script node is done, soft-deleting the script (whose content
    made it done) must NOT revert the node/cursor/issue — auto-sync is
    forward-only (module docstring)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seed = await _seed_common(conn)
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository

    script_repo = get_script_project_repository()
    script = await script_repo.get_or_create_for_episode(
        {
            "project_id": seed["project_id"],
            "team_id": seed["team_id"],
            "name": f"{_PREFIX}script",
            "status": "active",
            "created_by": str(seed["owner_id"]),
        },
        seed["episode_id"],
    )

    scene_repo = get_script_scene_repository()
    await scene_repo.create_with_content(
        {"script_id": script["id"]},
        [{"id": "el_1", "type": "action", "text": "INT. 客厅 - 日 一家人围坐。"}],
        actor=str(seed["owner_id"]),
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["script_node_id"]) == "done"
        cursor_before = await conn.fetchval(
            "SELECT current_node_id FROM episodes WHERE id=$1", seed["episode_id"]
        )
    finally:
        await conn.close()

    await script_repo.soft_delete(script["id"])

    conn = await asyncpg.connect(integration_db_url)
    try:
        assert await _node_status(conn, seed["script_node_id"]) == "done"
        assert await _issue_status(conn, seed["issue_id"]) == "done"
        cursor_after = await conn.fetchval(
            "SELECT current_node_id FROM episodes WHERE id=$1", seed["episode_id"]
        )
        assert cursor_after == cursor_before
    finally:
        await conn.close()


async def test_unbound_script_is_noop(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """A script with episode_id=NULL (created via plain create(), not
    get_or_create_for_episode) must not raise — _scope_for_script resolves
    to None and the hook is a silent no-op."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        project_id = await conn.fetchval(
            "INSERT INTO projects (name, owner_id) VALUES ($1, $2) RETURNING id",
            f"{_PREFIX}project_{uuid.uuid4().hex[:8]}",
            owner_id,
        )
    finally:
        await conn.close()

    from app.repositories.script_repository import get_script_project_repository

    script_repo = get_script_project_repository()
    script = await script_repo.create(
        {
            "project_id": project_id,
            "team_id": team_id,
            "name": f"{_PREFIX}unbound_script",
            "status": "active",
            "created_by": str(owner_id),
        }
    )
    assert script["id"] is not None
    assert script.get("episode_id") is None


async def test_project_wide_sync_completes_satisfied_episodes(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """B4 点火端点回归: seed 两集，Ep1 判据满足但节点 pending（模拟部署前就有产物），
    Ep2 空。调用 sync_project_surface_completion -> 返回 {"episodes": 2}。
    Ep1 Script 节点变 done，Ep2 不变（仍 pending）。"""
    # Setup: create project + owner + team
    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _seed_owner_and_team(conn)
        project_id = await conn.fetchval(
            "INSERT INTO projects (name, owner_id) VALUES ($1, $2) RETURNING id",
            f"{_PREFIX}project_{uuid.uuid4().hex[:8]}",
            owner_id,
        )
    finally:
        await conn.close()

    # Seed Ep1 with Script node (pending, has mirror issue)
    conn = await asyncpg.connect(integration_db_url)
    try:
        ep1_id = await conn.fetchval(
            "INSERT INTO episodes (project_id, title, sort_order) "
            "VALUES ($1, $2, 1) RETURNING id",
            project_id,
            "Episode 1",
        )
        ep1_script_node_id = await conn.fetchval(
            """
            INSERT INTO project_stage_nodes
                (project_id, episode_id, name, sort_order, parallel_group,
                 status, surface, review_required)
            VALUES ($1, $2, 'Script', 1, 1, 'pending', 'script', FALSE)
            RETURNING id
            """,
            project_id,
            ep1_id,
        )
        await conn.execute(
            "UPDATE episodes SET current_node_id=$1 WHERE id=$2",
            ep1_script_node_id,
            ep1_id,
        )
        # Create mirror issue for Ep1 Script node
        await conn.fetchval(
            """
            INSERT INTO issues
                (issue_number, identifier, title, status, priority,
                 origin_kind, origin_id, project_id, created_by_user_id)
            VALUES (
                (SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                $1, $2, 'in_progress', 'medium', 'project_stage', $3, $4, $5
            )
            RETURNING id
            """,
            f"{_PREFIX}{uuid.uuid4().hex[:8]}",
            f"{_PREFIX} ep1 mirror",
            f"project_stage:{project_id}:{ep1_script_node_id}",
            project_id,
            owner_id,
        )
    finally:
        await conn.close()

    # Seed Ep2 with Script node (empty/pending, no content, no mirror issue)
    conn = await asyncpg.connect(integration_db_url)
    try:
        ep2_id = await conn.fetchval(
            "INSERT INTO episodes (project_id, title, sort_order) "
            "VALUES ($1, $2, 2) RETURNING id",
            project_id,
            "Episode 2",
        )
        ep2_script_node_id = await conn.fetchval(
            """
            INSERT INTO project_stage_nodes
                (project_id, episode_id, name, sort_order, parallel_group,
                 status, surface, review_required)
            VALUES ($1, $2, 'Script', 1, 1, 'pending', 'script', FALSE)
            RETURNING id
            """,
            project_id,
            ep2_id,
        )
        await conn.execute(
            "UPDATE episodes SET current_node_id=$1 WHERE id=$2",
            ep2_script_node_id,
            ep2_id,
        )
    finally:
        await conn.close()

    # Create Ep1 script with content (satisfies script criterion)
    from app.repositories.script_repository import get_script_project_repository
    from app.repositories.script_scene_repository import get_script_scene_repository

    script_repo = get_script_project_repository()
    script_ep1 = await script_repo.get_or_create_for_episode(
        {
            "project_id": project_id,
            "team_id": team_id,
            "name": f"{_PREFIX}script_ep1",
            "status": "active",
            "created_by": str(owner_id),
        },
        ep1_id,
    )

    scene_repo = get_script_scene_repository()
    await scene_repo.create_with_content(
        {"script_id": script_ep1["id"]},
        [{"id": "el_1", "type": "action", "text": "INT. 客厅 - 日"}],
        actor=str(owner_id),
    )

    # Manually revert Ep1 Script node to pending (to simulate "criteria met but node stuck in pending")
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "UPDATE project_stage_nodes SET status='pending' WHERE id=$1",
            ep1_script_node_id,
        )
        # Also revert the mirror issue back to in_progress
        await conn.execute(
            "UPDATE issues SET status='in_progress' WHERE origin_id=$1",
            f"project_stage:{project_id}:{ep1_script_node_id}",
        )
    finally:
        await conn.close()

    # Call the service function directly (not via endpoint)
    from app.services.workflow.surface_completion import (
        sync_project_surface_completion,
    )

    result = await sync_project_surface_completion(str(project_id))

    # Assertions
    assert result == {"episodes": 2}, f"Expected {{'episodes': 2}}, got {result}"

    # Verify Ep1 Script node is now done
    conn = await asyncpg.connect(integration_db_url)
    try:
        ep1_node_status = await conn.fetchval(
            "SELECT status FROM project_stage_nodes WHERE id=$1",
            ep1_script_node_id,
        )
        assert ep1_node_status == "done", f"Expected Ep1 Script node to be 'done', got '{ep1_node_status}'"

        # Verify Ep2 Script node is still pending (not touched)
        ep2_node_status = await conn.fetchval(
            "SELECT status FROM project_stage_nodes WHERE id=$1",
            ep2_script_node_id,
        )
        assert (
            ep2_node_status == "pending"
        ), f"Expected Ep2 Script node to stay 'pending', got '{ep2_node_status}'"
    finally:
        await conn.close()
