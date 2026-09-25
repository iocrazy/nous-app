"""DB-backed guard for migration 505: orphan cleanup + agent FKs.

Only Postgres can answer these. The drift gate compares FK column pairs, not
``ON DELETE`` behaviour, and a stubbed session cannot show a cascade that did
or did not happen.

Everything runs inside a rolled-back transaction, and each test executes the
505 SQL itself first, so the result does not depend on whether the drift DB
already has 505 applied:

  (a) prod shape (no agent_skills FK, orphans present): the migration deletes
      orphan skill bindings, NULLs orphan meta agent_id without deleting the
      row, leaves live rows alone, and a second run succeeds (drift shape:
      every FK already present);
  (b) confdeltype is c / n / n / n for the four constraints;
  (c) hard-deleting an agent cascades its skill bindings and NULLs the meta
      binding while the slug stays;
  (d) deleting a parent run keeps the child (parent_run_id NULL), deleting a
      root keeps every descendant (root_run_id NULL).

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55499/drift \\
      uv run pytest tests/db/test_migration_505_agent_fks_integration.py
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 505 tests need a DB.",
)

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "505_agent_fks_backfill.sql"
)

_FKS = {
    "agent_skills_agent_id_fkey": "c",
    "conversation_ai_meta_agent_id_fkey": "n",
    "agent_runs_parent_run_id_fkey": "n",
    "agent_runs_root_run_id_fkey": "n",
}


def _sql() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


@pytest.fixture
async def tx():
    """One connection, one transaction, always rolled back."""
    conn = await asyncpg.connect(_TEST_DSN)
    t = conn.transaction()
    await t.start()
    try:
        yield conn
    finally:
        await t.rollback()
        await conn.close()


async def _user_and_team(pg) -> tuple[uuid.UUID, int]:
    uid = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", uid)
    team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3)"
        " RETURNING id",
        "Mig 505 Team",
        uid,
        uuid.uuid4().hex[:8],
    )
    return uid, team


async def _agent(pg, uid, slug: str | None = None) -> uuid.UUID:
    return await pg.fetchval(
        "INSERT INTO ai_agents (name, slug, user_id, created_by, is_system_preset)"
        " VALUES ('Mig 505 Fixture', $1, $2, $2, false) RETURNING id",
        slug or f"m505-{uuid.uuid4().hex[:10]}",
        uid,
    )


async def _skill(pg, uid) -> int:
    return await pg.fetchval(
        "INSERT INTO skills (name, created_by) VALUES ($1, $2) RETURNING id",
        f"m505-skill-{uuid.uuid4().hex[:8]}",
        uid,
    )


async def _meta(pg, uid, team, agent_id, slug: str) -> int:
    conv = await pg.fetchval(
        "INSERT INTO conversations (type, scope_id, created_by)"
        " VALUES ('direct_agent', $1, $2) RETURNING id",
        team,
        uid,
    )
    await pg.execute(
        "INSERT INTO conversation_ai_meta (conversation_id, agent_slug, agent_id)"
        " VALUES ($1, $2, $3)",
        conv,
        slug,
        agent_id,
    )
    return conv


async def _run(pg, agent_id, uid, *, parent=None, root=None) -> int:
    return await pg.fetchval(
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger, parent_run_id,"
        " root_run_id) VALUES ($1, $2, 'completed', 'chat', $3, $4) RETURNING id",
        agent_id,
        uid,
        parent,
        root,
    )


async def _prod_shape(pg) -> None:
    """Prod lacks both agent-id FKs (recon §0a); orphans can only exist then."""
    await pg.execute(
        "ALTER TABLE agent_skills DROP CONSTRAINT IF EXISTS agent_skills_agent_id_fkey"
    )
    await pg.execute(
        "ALTER TABLE conversation_ai_meta"
        " DROP CONSTRAINT IF EXISTS conversation_ai_meta_agent_id_fkey"
    )


@_skip
async def test_migration_cleans_orphans_and_is_rerunnable(tx):
    pg = tx
    uid, team = await _user_and_team(pg)
    live = await _agent(pg, uid, slug="m505-live")
    skill_a, skill_b = await _skill(pg, uid), await _skill(pg, uid)
    await _prod_shape(pg)
    ghost = uuid.uuid4()  # an agent id that no ai_agents row carries
    await pg.execute(
        "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $3), ($2, $3),"
        " ($2, $4)",
        live,
        ghost,
        skill_a,
        skill_b,
    )
    orphan_conv = await _meta(pg, uid, team, ghost, "plain-text-probe")
    live_conv = await _meta(pg, uid, team, live, "m505-live")
    # A soft-deleted agent's row still exists, so its binding is not an orphan.
    tomb = await _agent(pg, uid, slug="m505-tomb")
    await pg.execute("UPDATE ai_agents SET deleted_at = now() WHERE id = $1", tomb)
    tomb_conv = await _meta(pg, uid, team, tomb, "m505-tomb")

    await pg.execute(_sql())

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM agent_skills WHERE agent_id = $1", ghost
        )
        == 0
    ), "orphan skill bindings are deleted"
    assert (
        await pg.fetchval("SELECT count(*) FROM agent_skills WHERE agent_id = $1", live)
        == 1
    ), "live bindings stay"
    orphan = await pg.fetchrow(
        "SELECT agent_id, agent_slug FROM conversation_ai_meta"
        " WHERE conversation_id = $1",
        orphan_conv,
    )
    assert orphan is not None, "orphan meta row is kept, not deleted"
    assert orphan["agent_id"] is None
    assert orphan["agent_slug"] == "plain-text-probe"
    assert (
        await pg.fetchval(
            "SELECT agent_id FROM conversation_ai_meta WHERE conversation_id = $1",
            live_conv,
        )
        == live
    )
    assert (
        await pg.fetchval(
            "SELECT agent_id FROM conversation_ai_meta WHERE conversation_id = $1",
            tomb_conv,
        )
        == tomb
    ), "a soft-deleted agent's binding is kept (only missing rows are orphans)"

    # Second run: every FK already exists (the drift DB's shape).
    await pg.execute(_sql())

    rows = await pg.fetch(
        "SELECT conname, confdeltype::text AS confdeltype FROM pg_constraint"
        " WHERE conname = ANY($1)",
        list(_FKS),
    )
    assert {r["conname"]: r["confdeltype"] for r in rows} == _FKS


@_skip
async def test_agent_hard_delete_cascades_skills_and_nulls_meta(tx):
    pg = tx
    await pg.execute(_sql())
    uid, team = await _user_and_team(pg)
    doomed = await _agent(pg, uid, slug="m505-doomed")
    for skill in (await _skill(pg, uid), await _skill(pg, uid)):
        await pg.execute(
            "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
            doomed,
            skill,
        )
    conv = await _meta(pg, uid, team, doomed, "m505-doomed")

    await pg.execute("DELETE FROM ai_agents WHERE id = $1", doomed)

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM agent_skills WHERE agent_id = $1", doomed
        )
        == 0
    )
    meta = await pg.fetchrow(
        "SELECT agent_id, agent_slug FROM conversation_ai_meta"
        " WHERE conversation_id = $1",
        conv,
    )
    assert meta is not None, "the conversation keeps its meta row"
    assert meta["agent_id"] is None
    assert meta["agent_slug"] == "m505-doomed"


@_skip
async def test_run_delete_keeps_descendants(tx):
    pg = tx
    await pg.execute(_sql())
    uid, _ = await _user_and_team(pg)
    agent = await _agent(pg, uid)
    root = await _run(pg, agent, uid)
    child = await _run(pg, agent, uid, parent=root, root=root)
    sibling = await _run(pg, agent, uid, parent=root, root=root)
    grandchild = await _run(pg, agent, uid, parent=child, root=root)

    await pg.execute("DELETE FROM agent_runs WHERE id = $1", child)
    row = await pg.fetchrow(
        "SELECT parent_run_id, root_run_id FROM agent_runs WHERE id = $1", grandchild
    )
    assert row is not None, "deleting a parent keeps the child"
    assert row["parent_run_id"] is None
    assert row["root_run_id"] == root

    # Under root NO ACTION this DELETE raised 23503 (grandchild points at root).
    await pg.execute("DELETE FROM agent_runs WHERE id = $1", root)
    rows = await pg.fetch(
        "SELECT id, parent_run_id, root_run_id FROM agent_runs"
        " WHERE id = ANY($1::bigint[]) ORDER BY id",
        [sibling, grandchild],
    )
    assert len(rows) == 2, "deleting a root keeps every descendant"
    assert all(r["parent_run_id"] is None and r["root_run_id"] is None for r in rows)
