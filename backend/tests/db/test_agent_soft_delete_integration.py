"""Agent soft delete (mig 501) against the CI-built schema.

Why a real database: the point of the change is what Postgres does NOT do any
more. ``agent_runs.agent_id`` and ``agent_runs.parent_run_id`` are both
``ON DELETE CASCADE``, so the old hard delete removed the agent's own runs and
— one hop further — Delegate child runs owned by OTHER agents. A stubbed
session cannot show a cascade that did or did not happen, so the control case
runs the old ``DELETE`` inside a rolled-back transaction and watches the other
agent's child run disappear, then the soft delete runs for real and the same
row survives.

The reference counter is plain ORM; only the database can settle that the
terminal / hidden / skipped exclusions narrow the count instead of zeroing it.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixtures and
assertions; the repository goes through ``app.db.session`` repointed at the
same DSN. Skips cleanly when the DSN is unset.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — agent soft delete needs a DB.",
)


@pytest.fixture
async def orm_dsn():
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def user(pg):
    uid = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", uid)
    try:
        yield uid
    finally:
        # Children first: the parent/root FKs on agent_runs point inward.
        await pg.execute(
            "DELETE FROM agent_runs WHERE user_id = $1 AND parent_run_id IS NOT NULL",
            uid,
        )
        await pg.execute("DELETE FROM agent_runs WHERE user_id = $1", uid)
        await pg.execute("DELETE FROM issues WHERE created_by_user_id = $1", uid)
        await pg.execute("DELETE FROM projects WHERE owner_id = $1", uid)
        await pg.execute("DELETE FROM teams WHERE owner_id = $1", uid)
        await pg.execute("DELETE FROM ai_agents WHERE user_id = $1", uid)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", uid)


def _slug() -> str:
    return f"sd-{uuid.uuid4().hex[:10]}"


async def _agent(pg, user_id, *, slug: str | None = None, persistent=False):
    return await pg.fetchval(
        "INSERT INTO ai_agents (name, slug, user_id, created_by, is_system_preset,"
        " persistent) VALUES ($1, $2, $3, $3, false, $4) RETURNING id",
        "Soft Delete Fixture",
        slug or _slug(),
        user_id,
        persistent,
    )


async def _run(pg, agent_id, user_id, *, status="completed", parent=None):
    return await pg.fetchval(
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger, parent_run_id,"
        " root_run_id) VALUES ($1, $2, $3, 'chat', $4, $4) RETURNING id",
        agent_id,
        user_id,
        status,
        parent,
    )


async def _issue(pg, user_id, agent_id, *, status="todo", hidden=False) -> int:
    return await pg.fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, assignee_agent_id,
                               hidden_at)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, 'Soft delete fixture', $2, 'medium', 'manual', $3, $4, $5)
           RETURNING id""",
        f"SD-{uuid.uuid4().hex[:8]}",
        status,
        user_id,
        agent_id,
        dt.datetime.now(dt.timezone.utc) if hidden else None,
    )


# ── the cascade this migration stops ────────────────────────────────────


@_skip
async def test_soft_delete_keeps_runs_including_other_agents_child_runs(
    orm_dsn, pg, user
):
    from app.repositories.agent_repository import get_agent_repository

    doomed = await _agent(pg, user)
    other = await _agent(pg, user)
    own_run = await _run(pg, doomed, user)
    other_child = await _run(pg, other, user, parent=own_run)

    # Control: since mig 505 parent_run_id is SET NULL, so even a raw hard
    # delete keeps the OTHER agent's child run (it used to CASCADE away). The
    # soft delete below must still keep the agent's OWN run, which a hard
    # delete removes through agent_runs.agent_id CASCADE.
    tx = pg.transaction()
    await tx.start()
    try:
        await pg.execute("DELETE FROM ai_agents WHERE id = $1", doomed)
        child = await pg.fetchrow(
            "SELECT parent_run_id FROM agent_runs WHERE id = $1", other_child
        )
        assert child is not None, "control: parent_run_id SET NULL keeps the child"
        assert child["parent_run_id"] is None
        own = await pg.fetchval(
            "SELECT count(*) FROM agent_runs WHERE id = $1", own_run
        )
        assert own == 0, "control: agent_id CASCADE removes the agent's own run"
    finally:
        await tx.rollback()

    assert await get_agent_repository().delete_agent(doomed) is True

    row = await pg.fetchrow(
        "SELECT deleted_at, enabled FROM ai_agents WHERE id = $1", doomed
    )
    assert row is not None, "soft delete keeps the row"
    assert row["deleted_at"] is not None
    assert row["enabled"] is False
    runs = await pg.fetchval(
        "SELECT count(*) FROM agent_runs WHERE id = ANY($1::bigint[])",
        [own_run, other_child],
    )
    assert runs == 2, "own run and the other agent's child run both survive"

    # Deleting again is not a second success: the row is already gone from
    # the live set.
    assert await get_agent_repository().delete_agent(doomed) is False


@_skip
async def test_selection_reads_hide_deleted_history_reads_do_not(orm_dsn, pg, user):
    from app.repositories.agent_repository import get_agent_repository

    repo = get_agent_repository()
    slug = _slug()
    doomed = await _agent(pg, user, slug=slug, persistent=True)
    kept = await _agent(pg, user, persistent=True)
    assert await repo.delete_agent(doomed) is True

    assert await repo.get_by_slug(slug) is None
    tomb = await repo.get_by_slug(slug, include_deleted=True)
    assert tomb is not None and tomb["deleted_at"] is not None
    assert (await repo.get_by_id(doomed)) is not None, "history keeps resolving"

    listed = {a["id"] for a in await repo.list_accessible(user)}
    assert str(kept) in listed and str(doomed) not in listed
    workers = {w["id"] for w in await repo.list_persistent()}
    assert str(kept) in workers and str(doomed) not in workers
    assert slug not in await repo.list_all_slugs()


@_skip
async def test_slug_is_free_again_after_soft_delete(orm_dsn, pg, user):
    from app.repositories.agent_repository import get_agent_repository

    slug = _slug()
    first = await _agent(pg, user, slug=slug)
    # Positive control: while the first row is live the index still bites.
    with pytest.raises(asyncpg.UniqueViolationError):
        await _agent(pg, user, slug=slug)

    assert await get_agent_repository().delete_agent(first) is True
    second = await _agent(pg, user, slug=slug)
    live = await get_agent_repository().get_by_slug(slug)
    assert live is not None and live["id"] == str(second)


# ── live references ─────────────────────────────────────────────────────


@_skip
async def test_live_reference_counts_exclude_finished_work(orm_dsn, pg, user):
    from app.repositories.agent_references import count_live_agent_references

    agent = await _agent(pg, user)
    idle = await _agent(pg, user)

    await _issue(pg, user, agent, status="todo")
    await _issue(pg, user, agent, status="done")
    await _issue(pg, user, agent, status="cancelled")
    await _issue(pg, user, agent, status="in_progress", hidden=True)

    team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3)"
        " RETURNING id",
        "Soft Delete Team",
        user,
        uuid.uuid4().hex[:8],
    )
    pipeline = await pg.fetchval(
        "INSERT INTO issue_pipelines (team_id, name) VALUES ($1, 'P') RETURNING id",
        team,
    )
    await pg.execute(
        "INSERT INTO issue_pipeline_steps (pipeline_id, step_order, agent_id,"
        " title_template, prompt_template) VALUES ($1, 1, $2, 't', 'p')",
        pipeline,
        agent,
    )

    project = await pg.fetchval(
        "INSERT INTO projects (name, owner_id) VALUES ('Soft Delete Project', $1)"
        " RETURNING id",
        user,
    )
    await pg.execute(
        "INSERT INTO project_stage_nodes (project_id, name, sort_order, status,"
        " owner_agent_id) VALUES ($1, 'owned', 1, 'pending', $2),"
        " ($1, 'finished', 2, 'done', $2)",
        project,
        agent,
    )
    member_node = await pg.fetchval(
        "INSERT INTO project_stage_nodes (project_id, name, sort_order, status)"
        " VALUES ($1, 'member', 3, 'in_progress') RETURNING id",
        project,
    )
    await pg.execute(
        "INSERT INTO project_stage_node_members (node_id, agent_id) VALUES ($1, $2)",
        member_node,
        agent,
    )

    template = await pg.fetchval(
        "INSERT INTO workflow_templates (team_id, name) VALUES ($1, 'T') RETURNING id",
        team,
    )
    await pg.execute(
        "INSERT INTO workflow_template_nodes (template_id, name, sort_order,"
        " default_owner_agent_id) VALUES ($1, 'owned', 1, $2)",
        template,
        agent,
    )
    tmember = await pg.fetchval(
        "INSERT INTO workflow_template_nodes (template_id, name, sort_order)"
        " VALUES ($1, 'member', 2) RETURNING id",
        template,
    )
    await pg.execute(
        "INSERT INTO workflow_template_node_members (node_id, agent_id)"
        " VALUES ($1, $2)",
        tmember,
        agent,
    )

    await _run(pg, agent, user, status="running")
    await _run(pg, agent, user, status="completed")

    try:
        refs = await count_live_agent_references(agent)
        assert refs.as_counts() == {
            "issues": 1,
            "pipeline_steps": 1,
            "stage_nodes": 2,
            "template_nodes": 2,
            "running_runs": 1,
        }
        assert refs.in_use is True

        none = await count_live_agent_references(idle)
        assert none.in_use is False
        assert set(none.as_counts().values()) == {0}
    finally:
        await pg.execute("DELETE FROM workflow_templates WHERE id = $1", template)


# ── selection surfaces that query ai_agents directly ────────────────────


@_skip
async def test_skill_used_by_and_workforce_board_hide_deleted(orm_dsn, pg, user):
    from app.api.workforce_router import get_workforce_board
    from app.repositories.agent_repository import get_agent_repository
    from app.repositories.skill_repository import get_skill_repository

    doomed_slug, kept_slug = _slug(), _slug()
    doomed = await _agent(pg, user, slug=doomed_slug, persistent=True)
    kept = await _agent(pg, user, slug=kept_slug, persistent=True)
    skill = await pg.fetchval(
        "INSERT INTO skills (name, slug) VALUES ('Soft Delete Skill', $1) RETURNING id",
        _slug(),
    )
    await pg.execute(
        "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $3), ($2, $3)",
        doomed,
        kept,
        skill,
    )
    try:
        assert await get_agent_repository().delete_agent(doomed) is True

        repo = get_skill_repository()
        used_by = {a["slug"] for a in await repo.list_binding_agents(skill)}
        assert used_by == {kept_slug}
        mapped = await repo.map_binding_agents([skill])
        assert {a["slug"] for a in mapped[int(skill)]} == {kept_slug}

        board = await get_workforce_board(_auth=None)
        slugs = {a["slug"] for a in board["agents"]}
        assert kept_slug in slugs and doomed_slug not in slugs
    finally:
        await pg.execute("DELETE FROM agent_skills WHERE skill_id = $1", skill)
        await pg.execute("DELETE FROM skills WHERE id = $1", skill)
