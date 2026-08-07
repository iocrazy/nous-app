"""True-DB integration test for B3 per-episode workflow instantiation.

The unit suite (``test_workflow_instantiation.py``) covers the pure method
matrix + the FakeSession copy-freeze of a single project-level chain. B3's new
behaviour is inherently transactional — fan a template out into ONE node chain
PER episode, stamping ``episode_id`` and freezing ``surface`` on every node,
all in a SINGLE transaction so a mid-fan-out failure leaves ZERO nodes (the
all-or-nothing constraint from B2's autopilot audit: a half-bound project
whose legacy/per-episode detection flips mid-way silently stalls). Only a real
DB proves the rollback and the per-episode FK stamping, so this file is
gated on INTEGRATION_DATABASE_URL and skips cleanly when unset.

    INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55498/postgres" \\
        uv run pytest tests/integration/test_episode_instantiation_db.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_b3inst_"


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
    with patch.object(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    ):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def cleanup_test_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # project_stage_nodes.project_id / episodes.project_id are ON DELETE
        # CASCADE, so deleting the project clears its nodes + episodes. The
        # template is deleted by name prefix too.
        await conn.execute("DELETE FROM projects WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute(
            "DELETE FROM workflow_templates WHERE name LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _owner_and_team(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy projects.owner_id")
    team_id = await conn.fetchval("SELECT id FROM teams LIMIT 1")
    if not team_id:
        pytest.skip("No teams rows to satisfy workflow_templates.team_id")
    return uid, team_id


async def _seed_template(conn, team_id):
    """A 2-node template: node A surface='script' (sort 1), node B
    surface=NULL/deliverable (sort 2). Returns (template_id, [nodeA, nodeB])."""
    tid = await conn.fetchval(
        "INSERT INTO workflow_templates (team_id, name) VALUES ($1, $2) RETURNING id",
        team_id,
        f"{_PREFIX}Template {uuid.uuid4().hex[:8]}",
    )
    n_a = await conn.fetchval(
        """INSERT INTO workflow_template_nodes (template_id, name, sort_order, surface)
           VALUES ($1, 'Script', 1, 'script') RETURNING id""",
        tid,
    )
    n_b = await conn.fetchval(
        """INSERT INTO workflow_template_nodes (template_id, name, sort_order, surface)
           VALUES ($1, 'Deliverable', 2, NULL) RETURNING id""",
        tid,
    )
    return tid, [n_a, n_b]


async def _seed_project_with_episodes(conn, owner_id, n_episodes=2):
    project_id = await conn.fetchval(
        "INSERT INTO projects (name, owner_id) VALUES ($1, $2) RETURNING id",
        f"{_PREFIX}Project {uuid.uuid4().hex[:8]}",
        owner_id,
    )
    ep_ids = []
    for i in range(1, n_episodes + 1):
        eid = await conn.fetchval(
            """INSERT INTO episodes (project_id, title, sort_order)
               VALUES ($1, $2, $3) RETURNING id""",
            project_id,
            f"Episode {i}",
            i,
        )
        ep_ids.append(eid)
    return project_id, ep_ids


async def test_fans_out_one_chain_per_episode_with_frozen_surface(
    patched_engine, cleanup_test_rows, integration_db_url
):
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    result = await repo.instantiate_episode_chains(
        str(project_id), str(tid), [str(e) for e in ep_ids], method="ai"
    )

    # One chain (2 nodes) per episode, keyed by episode id.
    assert set(result.keys()) == {str(e) for e in ep_ids}
    for eid in ep_ids:
        chain = result[str(eid)]
        assert len(chain) == 2
        # every node in this chain carries THIS episode's id...
        assert all(str(n["episode_id"]) == str(eid) for n in chain)
        # ...and surface is frozen verbatim from the template (script + NULL).
        by_name = {n["name"]: n for n in chain}
        assert by_name["Script"]["surface"] == "script"
        assert by_name["Deliverable"]["surface"] is None

    conn = await asyncpg.connect(integration_db_url)
    try:
        total = await conn.fetchval(
            "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1", project_id
        )
        # 2 episodes x 2 nodes, and NONE left project-level (episode_id NULL).
        assert total == 4
        nulls = await conn.fetchval(
            "SELECT count(*) FROM project_stage_nodes "
            "WHERE project_id=$1 AND episode_id IS NULL",
            project_id,
        )
        assert nulls == 0
    finally:
        await conn.close()


async def test_fan_out_is_atomic_no_partial_chain_on_bad_episode(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """A non-existent episode_id violates the FK on insert; the whole fan-out
    must roll back, leaving ZERO nodes — never a partial chain for the good
    episode (the half-bound state B2's audit forbids)."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 1)
    finally:
        await conn.close()

    good_ep = str(ep_ids[0])
    bad_ep = "999999999999999999"  # no such episode → FK violation

    from sqlalchemy.exc import IntegrityError

    repo = get_project_stage_nodes_repository()
    # Narrowed to IntegrityError (not bare Exception) so this fails during RED
    # on the missing-method AttributeError instead of green-washing it.
    with pytest.raises(IntegrityError):
        await repo.instantiate_episode_chains(
            str(project_id), str(tid), [good_ep, bad_ep], method="ai"
        )

    conn = await asyncpg.connect(integration_db_url)
    try:
        total = await conn.fetchval(
            "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1", project_id
        )
        assert total == 0  # atomic: the good episode's chain rolled back too
    finally:
        await conn.close()


async def test_fan_out_is_idempotent_and_expect_fresh_raises(
    patched_engine, cleanup_test_rows, integration_db_url
):
    from app.repositories.project_stage_nodes_repository import (
        WorkflowAlreadyInstantiated,
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    eids = [str(e) for e in ep_ids]
    await repo.instantiate_episode_chains(str(project_id), str(tid), eids, method="ai")

    # Second call without expect_fresh must not double the nodes.
    await repo.instantiate_episode_chains(str(project_id), str(tid), eids, method="ai")
    conn = await asyncpg.connect(integration_db_url)
    try:
        total = await conn.fetchval(
            "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1", project_id
        )
        assert total == 4
    finally:
        await conn.close()

    # expect_fresh on an already-instantiated project raises (attach 409 path).
    with pytest.raises(WorkflowAlreadyInstantiated):
        await repo.instantiate_episode_chains(
            str(project_id), str(tid), eids, method="ai", expect_fresh=True
        )


async def test_instantiate_project_workflow_fans_out_and_sets_per_episode_cursors(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """The service entry (attach/create path) writes the project binding, fans
    the template out to EVERY episode, and sets each episode's own cursor to
    its first active node — not the legacy single project-level cursor."""
    from app.services.workflow.instantiation import instantiate_project_workflow

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
    finally:
        await conn.close()

    await instantiate_project_workflow(
        str(project_id), str(tid), method="ai", user_id=str(owner_id)
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        # (1) binding written
        row = await conn.fetchrow(
            "SELECT workflow_template_id, workflow_method FROM projects WHERE id=$1",
            project_id,
        )
        assert row["workflow_template_id"] == tid
        assert row["workflow_method"] == "ai"
        # (2) every episode got its chain, none left project-level
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 0
        )
        # (3) each episode's own cursor points at a node of THAT episode
        for eid in ep_ids:
            cnt = await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id=$2",
                project_id,
                eid,
            )
            assert cnt == 2
            cur = await conn.fetchval(
                "SELECT current_node_id FROM episodes WHERE id=$1", eid
            )
            assert cur is not None
            assert (
                await conn.fetchval(
                    "SELECT episode_id FROM project_stage_nodes WHERE id=$1", cur
                )
                == eid
            )
    finally:
        await conn.close()


async def test_single_episode_workflow_service_sets_new_episode_cursor(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """The new-episode trigger service: a bound project gets its newly added
    episode instantiated + that episode's own cursor set. A project with NO
    binding is a clean no-op."""
    from app.services.workflow.instantiation import (
        instantiate_single_episode_workflow,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, _ = await _seed_project_with_episodes(conn, owner_id, 0)
        # unbound project first: adding an episode must be a no-op
        unbound_ep = await conn.fetchval(
            "INSERT INTO episodes (project_id, title, sort_order) "
            "VALUES ($1, 'Ep0', 1) RETURNING id",
            project_id,
        )
    finally:
        await conn.close()

    nodes = await instantiate_single_episode_workflow(
        str(project_id), str(unbound_ep), user_id=str(owner_id)
    )
    assert nodes == []  # no binding → no-op

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "UPDATE projects SET workflow_template_id=$1, workflow_method='ai' "
            "WHERE id=$2",
            tid,
            project_id,
        )
        bound_ep = await conn.fetchval(
            "INSERT INTO episodes (project_id, title, sort_order) "
            "VALUES ($1, 'Ep1', 2) RETURNING id",
            project_id,
        )
    finally:
        await conn.close()

    nodes = await instantiate_single_episode_workflow(
        str(project_id), str(bound_ep), user_id=str(owner_id)
    )
    assert len(nodes) == 2

    conn = await asyncpg.connect(integration_db_url)
    try:
        cur = await conn.fetchval(
            "SELECT current_node_id FROM episodes WHERE id=$1", bound_ep
        )
        assert cur is not None
        assert (
            await conn.fetchval(
                "SELECT episode_id FROM project_stage_nodes WHERE id=$1", cur
            )
            == bound_ep
        )
    finally:
        await conn.close()


async def test_reinstantiate_project_service_ignites_legacy_project(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """The backfill SERVICE ignites a sleeping legacy project end-to-end: infers
    the template from its existing nodes, converts to per-episode chains, writes
    the project binding, and sets each episode's cursor."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.workflow.instantiation import (
        reinstantiate_project_per_episode,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
    finally:
        await conn.close()

    # Sleeping legacy state: one project-level chain, no binding, no cursors.
    repo = get_project_stage_nodes_repository()
    await repo.instantiate_from_template(str(project_id), str(tid), method="ai")

    result = await reinstantiate_project_per_episode(
        str(project_id), user_id=str(owner_id)
    )
    assert result["converted"] is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        # legacy gone, per-episode chains in, binding written
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT workflow_template_id FROM projects WHERE id=$1", project_id
            )
            == tid
        )
        for eid in ep_ids:
            cur = await conn.fetchval(
                "SELECT current_node_id FROM episodes WHERE id=$1", eid
            )
            assert cur is not None
            assert (
                await conn.fetchval(
                    "SELECT episode_id FROM project_stage_nodes WHERE id=$1", cur
                )
                == eid
            )
    finally:
        await conn.close()

    # Idempotent: already per-episode → reports not converted, no doubling.
    result2 = await reinstantiate_project_per_episode(
        str(project_id), user_id=str(owner_id)
    )
    assert result2["converted"] is False
    conn = await asyncpg.connect(integration_db_url)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1",
                project_id,
            )
            == 4
        )
    finally:
        await conn.close()


async def test_reinstantiate_empty_template_never_drops_legacy_chain(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """Review I1: a template with zero nodes must NOT delete the legacy chain.
    The bits-empty short-circuit has to happen BEFORE the DELETE, or the
    conversion becomes silent data loss (legacy gone, nothing recreated)."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
        # empty template (no nodes) — the data-loss trigger
        empty_tid = await conn.fetchval(
            "INSERT INTO workflow_templates (team_id, name) VALUES ($1, $2) "
            "RETURNING id",
            team_id,
            f"{_PREFIX}Empty {uuid.uuid4().hex[:8]}",
        )
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    await repo.instantiate_from_template(str(project_id), str(tid), method="ai")

    # Reinstantiate against the EMPTY template — must not touch the legacy chain.
    await repo.reinstantiate_legacy_as_episodes(
        str(project_id), str(empty_tid), [str(e) for e in ep_ids], method="ai"
    )
    conn = await asyncpg.connect(integration_db_url)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 2  # legacy chain preserved — no data loss
        )
    finally:
        await conn.close()


async def test_instantiate_episode_chains_refuses_legacy_populated_project(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """Review I2: fanning per-episode chains onto a project that still holds
    legacy (episode_id NULL) nodes would create the forbidden half-bound state.
    Defense-in-depth: refuse it."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 1)
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    await repo.instantiate_from_template(str(project_id), str(tid), method="ai")

    with pytest.raises(ValueError):
        await repo.instantiate_episode_chains(
            str(project_id), str(tid), [str(ep_ids[0])], method="ai"
        )
    conn = await asyncpg.connect(integration_db_url)
    try:
        # nothing added on top of the legacy chain
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NOT NULL",
                project_id,
            )
            == 0
        )
    finally:
        await conn.close()


async def test_add_node_refuses_per_episode_project(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """Review I3: adding a project-level (episode_id NULL) node to a per-episode
    project manufactures the silent-stall state B2's autopilot detection falls
    into. add_project_node must fail loudly instead."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.workflow.node_mutations import add_project_node

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 1)
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    # Make it per-episode.
    await repo.instantiate_episode_chains(
        str(project_id), str(tid), [str(ep_ids[0])], method="ai"
    )

    with pytest.raises(ValueError):
        await add_project_node(str(project_id), name="New stage", sort_order=99)


async def test_reinstantiate_legacy_as_episodes_is_atomic_and_idempotent(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """The backfill repo method converts a legacy project-level chain
    (episode_id NULL) into per-episode chains in ONE transaction: the legacy
    delete + the fan-out either both land or both roll back (never the mixed
    NULL/non-NULL state B2 forbids). Idempotent once already per-episode."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 2)
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()

    # Start in the legacy state: one project-level chain, episode_id all NULL.
    await repo.instantiate_from_template(str(project_id), str(tid), method="ai")
    conn = await asyncpg.connect(integration_db_url)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 2
        )
    finally:
        await conn.close()

    # Atomicity: a bad episode mid-fan-out must roll BACK the legacy delete too.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await repo.reinstantiate_legacy_as_episodes(
            str(project_id),
            str(tid),
            [str(ep_ids[0]), "999999999999999999"],
            method="ai",
        )
    conn = await asyncpg.connect(integration_db_url)
    try:
        # legacy chain untouched — rollback preserved it
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 2
        )
    finally:
        await conn.close()

    # Happy path: legacy gone, per-episode chains in.
    await repo.reinstantiate_legacy_as_episodes(
        str(project_id), str(tid), [str(e) for e in ep_ids], method="ai"
    )
    conn = await asyncpg.connect(integration_db_url)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes "
                "WHERE project_id=$1 AND episode_id IS NULL",
                project_id,
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1",
                project_id,
            )
            == 4
        )
    finally:
        await conn.close()

    # Idempotent: already per-episode → no-op, no doubling.
    await repo.reinstantiate_legacy_as_episodes(
        str(project_id), str(tid), [str(e) for e in ep_ids], method="ai"
    )
    conn = await asyncpg.connect(integration_db_url)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_stage_nodes WHERE project_id=$1",
                project_id,
            )
            == 4
        )
    finally:
        await conn.close()


async def test_single_episode_chain_reads_project_binding(
    patched_engine, cleanup_test_rows, integration_db_url
):
    """A newly added episode gets its chain from the project's stored binding
    (workflow_template_id + workflow_method), not a caller-passed template."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id, team_id = await _owner_and_team(conn)
        tid, _tpl = await _seed_template(conn, team_id)
        project_id, ep_ids = await _seed_project_with_episodes(conn, owner_id, 1)
        # Simulate attach having stored the binding + fanned out to ep1.
        await conn.execute(
            "UPDATE projects SET workflow_template_id=$1, workflow_method='ai' "
            "WHERE id=$2",
            tid,
            project_id,
        )
        # A new episode arrives later.
        new_ep = await conn.fetchval(
            """INSERT INTO episodes (project_id, title, sort_order)
               VALUES ($1, 'Episode 2', 2) RETURNING id""",
            project_id,
        )
    finally:
        await conn.close()

    repo = get_project_stage_nodes_repository()
    # ep1 already fanned out separately; instantiate only the new episode.
    await repo.instantiate_episode_chains(
        str(project_id), str(tid), [str(ep_ids[0])], method="ai"
    )
    chain = await repo.instantiate_single_episode_chain(str(project_id), str(new_ep))

    assert len(chain) == 2
    assert all(str(n["episode_id"]) == str(new_ep) for n in chain)

    conn = await asyncpg.connect(integration_db_url)
    try:
        new_ep_count = await conn.fetchval(
            "SELECT count(*) FROM project_stage_nodes "
            "WHERE project_id=$1 AND episode_id=$2",
            project_id,
            new_ep,
        )
        assert new_ep_count == 2
    finally:
        await conn.close()
