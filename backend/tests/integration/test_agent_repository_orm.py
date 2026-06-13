"""Integration tests for AgentRepositoryOrm (Phase 2 PILOT) against real PG.

The agent repository runs on the SQLAlchemy 2.0 ORM session layer now —
``read_scope()`` for reads, ``write_scope()`` (which COMMITS) for writes. These
tests run real SQL against a real Postgres to prove the migration is invisible
to call sites AND that STRATEGY-C value-type parity holds.

THE STRATEGY-C IRON RULE (what these tests pin)
===============================================
The ORM returns NATIVE ``uuid.UUID`` for the id / user_id / created_by columns,
whereas the supabase-py REST baseline returned STRINGS. Python-layer
type-sensitive consumers break on the native type:

  - ``UUID(agent["id"])``           (prompt_composer / delegate_tool / chat_wiring
                                     / workforce_router) → TypeError on a native
                                     uuid.UUID (UUID(UUID) is illegal).
  - ``agent["id"]`` as a DICT KEY   (workforce_router board) against REST-str
                                     keys → silent miss.
  - inserting ``agent["id"]`` into  ai_sessions via supabase-py
                                     (ai_library_chat_service) → json.dumps(UUID)
                                     raises TypeError.

So the repo coerces id / user_id / created_by → str at the dict boundary. The
``*_regression_*`` tests prove the coercion is LOAD-BEARING: the raw ORM dict
(without coercion) breaks the EXACT consumer path, the coerced dict does not.

team_id / project_id (bigint) are LEFT as native int (the 5.3 trap — coercing
them to str silently zeroes team/project scope, whose consumer does int()/bare
compare). The ``*_bigint_*`` test pins that they stay int.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub schema.
Skips otherwise. Use the dev stack:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_agent_repository_orm.py -v

Seed ai_agents rows are tagged with a per-run name prefix for predictable
cleanup; the fixture deletes them (and cascades agent_skills /
ai_agent_versions via FK) after each test even on failure.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from uuid import UUID

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_AGENT_PREFIX = "__test_orm_agent_"


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
    """Delete ai_agents tagged with the test prefix (agent_skills /
    ai_agent_versions cascade-delete via the agent_id FK)."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM ai_agents WHERE name LIKE $1", _AGENT_PREFIX + "%"
        )
    finally:
        await conn.close()


async def _seed_agent(conn, **overrides) -> dict:
    """INSERT one minimal ai_agents row and return it as a dict.

    Only ``name`` is NOT NULL without a server_default; id / current_version /
    fallback_models / persistent are DB-defaulted. Caller can override any
    column (slug, persistent, user_id, team_id, project_id, …)."""
    defaults = {"name": f"{_AGENT_PREFIX}{uuid.uuid4().hex[:8]}"}
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO ai_agents ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy created_by/user_id FKs")
    return uid


async def _seed_skill(conn) -> int:
    """INSERT a throwaway skills row, return its BIGINT id (for binding tests)."""
    return await conn.fetchval(
        "INSERT INTO skills (slug, name) VALUES ($1, $2) RETURNING id",
        f"{_AGENT_PREFIX}skill_{uuid.uuid4().hex[:8]}",
        "Test Skill",
    )


def _repo():
    from app.repositories.agent_repository_orm import AgentRepositoryOrm

    return AgentRepositoryOrm()


# ─── Reads: dict shape + strategy-C value-type parity ──────────────────


async def test_get_by_slug_shape_and_uuid_str_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_by_slug returns a SELECT-* dict; id / user_id / created_by surface
    as STRINGS (strategy-C), matching the REST baseline. Proves the
    UUID(agent["id"]) consumer path works (does NOT raise)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        slug = f"slug_{uuid.uuid4().hex[:8]}"
        seeded = await _seed_agent(conn, slug=slug, user_id=user_id, created_by=user_id)
    finally:
        await conn.close()

    result = await _repo().get_by_slug(slug)
    assert result is not None
    # SELECT * parity — full column set present.
    for col in ("id", "name", "slug", "persistent", "current_version", "team_id"):
        assert col in result, f"missing column {col} in SELECT * dict"
    # Strategy-C: uuid columns are STR.
    assert type(result["id"]) is str
    assert type(result["user_id"]) is str
    assert type(result["created_by"]) is str
    # The consumer path (prompt_composer / delegate_tool / chat_wiring /
    # workforce_router) does UUID(agent["id"]) — must NOT raise.
    assert UUID(result["id"]) == seeded["id"]
    # And the == comparison the consumers do works against a uuid-str.
    assert result["user_id"] == str(seeded["user_id"])


async def test_get_by_slug_regression_native_uuid_breaks_consumer(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """REGRESSION PROOF (the iron rule): WITHOUT strategy-C coercion the dict
    carries a native uuid.UUID, and the real consumer op UUID(agent["id"])
    raises TypeError. WITH coercion it is a str and does not. A parity test
    that passed both with and without coercion would be worthless — this one
    fails without it."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AiAgents
    from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

    conn = await asyncpg.connect(integration_db_url)
    try:
        slug = f"slug_{uuid.uuid4().hex[:8]}"
        await _seed_agent(conn, slug=slug)
    finally:
        await conn.close()

    # Build the UNCOERCED dict exactly as the boundary would WITHOUT strategy C.
    name_to_attr = _name_to_attr(AiAgents)
    async with read_scope() as session:
        row = (
            (await session.execute(select(AiAgents).where(AiAgents.slug == slug)))
            .scalars()
            .first()
        )
    uncoerced = _orm_obj_to_dict(row, name_to_attr)
    assert isinstance(uncoerced["id"], uuid.UUID)  # native — the danger
    # ``UUID(uuid.UUID(...))`` is illegal — CPython passes the UUID as the
    # ``hex`` positional and blows up (TypeError or AttributeError depending on
    # the version). EITHER proves the native-UUID consumer path is broken.
    with pytest.raises((TypeError, AttributeError)):
        # This is the EXACT op prompt_composer.py:133 / delegate_tool.py:161 do.
        UUID(uncoerced["id"])  # type: ignore[arg-type]

    # The repo's coerced dict makes the same op succeed.
    coerced = await _repo().get_by_slug(slug)
    assert UUID(coerced["id"])  # no raise


async def test_get_by_slug_dict_key_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """workforce_router buckets runs by ``agent["id"]`` used as a DICT KEY,
    cross-referenced against REST-str keys. The coerced str key must match a
    str key built from the same uuid; the native UUID key would silently miss."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        slug = f"slug_{uuid.uuid4().hex[:8]}"
        seeded = await _seed_agent(conn, slug=slug)
    finally:
        await conn.close()

    result = await _repo().get_by_slug(slug)
    # Simulate the REST-str-keyed bucket dict the board builds from other tables.
    rest_str_keyed = {str(seeded["id"]): "bucket"}
    assert rest_str_keyed.get(result["id"]) == "bucket"  # str key hits


async def test_get_by_slug_timestamp_iso_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """timestamptz columns (created_at / updated_at) surface as ISO STRINGS,
    matching the REST baseline. Proves the prompt_composer fingerprint consumer
    op ``str(agent["updated_at"])`` yields the ISO ``T``-separated shape REST
    would (not the SPACE-separated ``str(datetime)`` shape)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        slug = f"slug_{uuid.uuid4().hex[:8]}"
        await _seed_agent(conn, slug=slug)
    finally:
        await conn.close()

    result = await _repo().get_by_slug(slug)
    for col in ("created_at", "updated_at"):
        assert type(result[col]) is str, f"{col} must be an ISO str (REST parity)"
        # Round-trips as ISO — i.e. it IS isoformat output, not arbitrary text.
        assert datetime.fromisoformat(result[col])
        # The fingerprint op: ISO uses a 'T' separator, never a space.
        assert "T" in result[col]
        assert " " not in result[col]


async def test_get_by_slug_timestamp_regression_native_datetime_diverges(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """REGRESSION PROOF (the iron rule): WITHOUT the ISO coercion the dict
    carries a native ``datetime`` whose ``str()`` (the EXACT prompt_composer.
    py:440 fingerprint op) uses a SPACE separator — a DIFFERENT byte string from
    REST's ISO. WITH coercion the fingerprint op yields the ISO shape REST did.
    A parity test that passed both with and without coercion would be worthless:
    this asserts the two ``str()`` outputs DIFFER, so the coercion is
    load-bearing for prompt-cache stability across the flip."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AiAgents
    from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

    conn = await asyncpg.connect(integration_db_url)
    try:
        slug = f"slug_{uuid.uuid4().hex[:8]}"
        await _seed_agent(conn, slug=slug)
    finally:
        await conn.close()

    # UNCOERCED dict (boundary WITHOUT the timestamp rule): native datetime.
    name_to_attr = _name_to_attr(AiAgents)
    async with read_scope() as session:
        row = (
            (await session.execute(select(AiAgents).where(AiAgents.slug == slug)))
            .scalars()
            .first()
        )
    uncoerced = _orm_obj_to_dict(row, name_to_attr)
    assert isinstance(uncoerced["updated_at"], datetime)  # native — the danger

    # The fingerprint consumer op str(updated_at) on the UNCOERCED datetime is a
    # DIFFERENT byte string than on the coerced ISO str — this is the prompt-
    # cache shift the coercion prevents.
    coerced = await _repo().get_by_slug(slug)
    assert str(uncoerced["updated_at"]) != str(coerced["updated_at"])
    # And the coerced value matches what REST/.isoformat() produces exactly.
    assert str(coerced["updated_at"]) == uncoerced["updated_at"].isoformat()


async def test_get_by_id_and_missing(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_by_id(UUID) returns the row; unknown id → None."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        seeded = await _seed_agent(conn)
    finally:
        await conn.close()

    result = await _repo().get_by_id(seeded["id"])
    assert result is not None
    assert type(result["id"]) is str
    assert UUID(result["id"]) == seeded["id"]
    assert await _repo().get_by_id(uuid.uuid4()) is None


async def test_list_persistent_projection_and_uuid_str(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_persistent returns the 5-col projection (id/slug/name/description/
    model), id as STR, sorted by slug, persistent-only."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        a = await _seed_agent(conn, slug="zzz_persist", persistent=True)
        b = await _seed_agent(conn, slug="aaa_persist", persistent=True)
        await _seed_agent(conn, slug="not_persist", persistent=False)
    finally:
        await conn.close()

    rows = await _repo().list_persistent()
    ours = [r for r in rows if str(r["id"]) in {str(a["id"]), str(b["id"])}]
    assert len(ours) == 2
    sample = ours[0]
    assert set(sample.keys()) == {"id", "slug", "name", "description", "model"}
    assert type(sample["id"]) is str
    # Sorted by slug: aaa before zzz.
    slugs = [r["slug"] for r in rows if r["slug"] in ("aaa_persist", "zzz_persist")]
    assert slugs == sorted(slugs)
    # Non-persistent excluded.
    assert all(r["slug"] != "not_persist" for r in rows)
    # The prompt_composer worker-vs-self comparison (w.get("id") != agent.get
    # ("id")) is str-vs-str — pin that both sides are str.
    self_dict = await _repo().get_by_id(a["id"])
    assert (sample["id"] != self_dict["id"]) == (str(sample["id"]) != str(a["id"]))


async def test_list_accessible_union_and_bigint_stays_int(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_accessible unions system-preset + own + team + project; and the
    BIGINT team_id / project_id stay NATIVE int (the 5.3 trap). The consumer
    does int(team_id) / bare-int dict lookup — a str would zero the scope."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        # Own agent with a team_id + project_id set (bigint).
        own = await _seed_agent(
            conn, user_id=user_id, team_id=4242, project_id=777, slug="own"
        )
        # System preset (visible to everyone).
        preset = await _seed_agent(conn, is_system_preset=True, slug="preset")
        # A team-scoped agent owned by someone else, visible via team_ids.
        team_agent = await _seed_agent(conn, team_id=9999, slug="team")
    finally:
        await conn.close()

    rows = await _repo().list_accessible(
        user_id=user_id, team_ids=[9999], project_ids=[]
    )
    ids = {str(r["id"]) for r in rows}
    assert str(own["id"]) in ids  # own
    assert str(preset["id"]) in ids  # system preset
    assert str(team_agent["id"]) in ids  # team-scoped

    own_row = next(r for r in rows if str(r["id"]) == str(own["id"]))
    # BIGINT stays int — NOT str. This is the load-bearing 5.3 assertion.
    assert type(own_row["team_id"]) is int
    assert own_row["team_id"] == 4242  # bare-int compare the consumer does
    assert type(own_row["project_id"]) is int
    # The consumer does int(team_id) — works on int (and would silently break
    # the scope name-map lookup if it were "4242").
    assert int(own_row["team_id"]) == 4242


async def test_get_skill_ids_int_and_order(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_skill_ids returns ordered list[int] of ENABLED bindings; skill_id is
    BIGINT and is consumed as int both ways (no str coercion)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent = await _seed_agent(conn)
        s1 = await _seed_skill(conn)
        s2 = await _seed_skill(conn)
        s3 = await _seed_skill(conn)
        # Insert out of order; enabled flags mixed.
        await conn.execute(
            "INSERT INTO agent_skills (agent_id, skill_id, sort_order, enabled) "
            "VALUES ($1,$2,1,true),($1,$3,0,true),($1,$4,2,false)",
            agent["id"],
            s1,
            s2,
            s3,
        )
    finally:
        await conn.close()

    ids = await _repo().get_skill_ids(agent["id"])
    # Ordered by sort_order, enabled-only (s3 excluded).
    assert ids == [s2, s1]
    assert all(type(i) is int for i in ids)


# ─── Writes: COMMIT + parity ────────────────────────────────────────────


async def test_update_fields_commits_and_returns_str_id(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_fields PERSISTS (write_scope commits) and returns the updated
    row dict with strategy-C parity. A fresh asyncpg read sees the new value
    (proves no silent rollback)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent = await _seed_agent(conn, paused_reason=None)
    finally:
        await conn.close()

    out = await _repo().update_fields(agent["id"], {"paused_reason": "manual"})
    assert out["paused_reason"] == "manual"
    assert type(out["id"]) is str  # strategy-C parity on the returned dict

    # Fresh connection — confirm the UPDATE actually committed.
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT paused_reason FROM ai_agents WHERE id = $1", agent["id"]
        )
    finally:
        await conn.close()
    assert persisted == "manual"


async def test_update_fields_no_match_returns_empty(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_fields on an unknown id returns {} (no row matched)."""
    out = await _repo().update_fields(uuid.uuid4(), {"paused_reason": "manual"})
    assert out == {}


async def test_insert_commits_and_returns_str_id(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """insert PERSISTS and returns the inserted row dict with str id."""
    name = f"{_AGENT_PREFIX}{uuid.uuid4().hex[:8]}"
    out = await _repo().insert({"name": name, "slug": "inserted"})
    assert type(out["id"]) is str
    assert out["name"] == name

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM ai_agents WHERE id = $1", UUID(out["id"])
        )
    finally:
        await conn.close()
    assert persisted == name


async def test_update_skill_bindings_replace_commits(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_skill_bindings replaces all bindings atomically + commits;
    idempotent (re-run with same set converges). Read-back via get_skill_ids."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent = await _seed_agent(conn)
        s1 = await _seed_skill(conn)
        s2 = await _seed_skill(conn)
    finally:
        await conn.close()

    await _repo().update_skill_bindings(agent["id"], [s2, s1])
    assert await _repo().get_skill_ids(agent["id"]) == [s2, s1]  # order preserved

    # Idempotent re-run with a different set replaces cleanly.
    await _repo().update_skill_bindings(agent["id"], [s1])
    assert await _repo().get_skill_ids(agent["id"]) == [s1]

    # Empty set clears all bindings.
    await _repo().update_skill_bindings(agent["id"], [])
    assert await _repo().get_skill_ids(agent["id"]) == []


async def test_update_skill_bindings_concurrent_same_set_no_conflict(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Two concurrent re-binds of the SAME set must converge without raising.

    Regression for the startup race: gateway + worker (or multiple uvicorn
    workers) both run seed_loader and re-bind the same agent concurrently.
    The old delete-then-insert raced — the second committer's plain INSERT
    hit agent_skills_pkey UniqueViolation. The ON CONFLICT upsert path makes
    each row write atomic, so both calls succeed and the final state is exact.
    """
    import asyncio

    conn = await asyncpg.connect(integration_db_url)
    try:
        agent = await _seed_agent(conn)
        s1 = await _seed_skill(conn)
        s2 = await _seed_skill(conn)
    finally:
        await conn.close()

    # Run N identical re-binds concurrently — each its own write_scope()
    # session/connection, so this is genuine concurrency. With the old
    # delete-then-insert at least one would raise UniqueViolation.
    results = await asyncio.gather(
        *[_repo().update_skill_bindings(agent["id"], [s1, s2]) for _ in range(4)],
        return_exceptions=True,
    )
    raised = [r for r in results if isinstance(r, Exception)]
    assert not raised, f"concurrent re-bind raised: {raised}"

    # Final state is exactly the desired set, in order, with no duplicates.
    assert await _repo().get_skill_ids(agent["id"]) == [s1, s2]


async def test_update_fields_versioned_snapshots_and_commits(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update_fields_versioned snapshots the OLD behavioral content into
    ai_agent_versions, bumps current_version, and commits. No-op when nothing
    changed; ValueError when the agent is missing."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent = await _seed_agent(conn, agent_md="v1 body", current_version=1)
    finally:
        await conn.close()

    # Change a tracked field → snapshot + bump.
    await _repo().update_fields_versioned(
        agent["id"], {"agent_md": "v2 body"}, notes="edit"
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        cur = await conn.fetchval(
            "SELECT current_version FROM ai_agents WHERE id = $1", agent["id"]
        )
        live_body = await conn.fetchval(
            "SELECT agent_md FROM ai_agents WHERE id = $1", agent["id"]
        )
        snap = await conn.fetchrow(
            "SELECT version_number, agent_md FROM ai_agent_versions "
            "WHERE agent_id = $1",
            agent["id"],
        )
    finally:
        await conn.close()
    assert cur == 2  # bumped + committed
    assert live_body == "v2 body"
    assert snap is not None
    assert snap["version_number"] == 1  # snapshotted the OLD version
    assert snap["agent_md"] == "v1 body"  # snapshotted the OLD body

    # No-op: re-applying the SAME value writes nothing new.
    await _repo().update_fields_versioned(agent["id"], {"agent_md": "v2 body"})
    conn = await asyncpg.connect(integration_db_url)
    try:
        cur2 = await conn.fetchval(
            "SELECT current_version FROM ai_agents WHERE id = $1", agent["id"]
        )
        snap_count = await conn.fetchval(
            "SELECT count(*) FROM ai_agent_versions WHERE agent_id = $1", agent["id"]
        )
    finally:
        await conn.close()
    assert cur2 == 2  # no second bump
    assert snap_count == 1  # no second snapshot

    # Missing agent → ValueError.
    with pytest.raises(ValueError):
        await _repo().update_fields_versioned(uuid.uuid4(), {"agent_md": "x"})


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    """With USE_ORM_AGENTS off (default), the factory returns the legacy REST
    AgentRepository — the ORM swap is opt-in and the REST path is unchanged."""
    from app.core.config import settings
    from app.repositories import agent_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_AGENTS", False)
    repo = mod.get_agent_repository()
    assert type(repo) is mod.AgentRepository
    # Crucially NOT the ORM subclass.
    from app.repositories.agent_repository_orm import AgentRepositoryOrm

    assert not isinstance(repo, AgentRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    """With USE_ORM_AGENTS on AND the engine configured, the factory returns
    the ORM subclass."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import agent_repository as mod
    from app.repositories.agent_repository_orm import AgentRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_AGENTS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_agent_repository()
    assert isinstance(repo, AgentRepositoryOrm)
