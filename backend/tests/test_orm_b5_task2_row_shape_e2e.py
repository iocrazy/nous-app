"""Real-SQLAlchemy-Result regression guard for the row-shape class of bug
(Phase B5 Task 2 — workflows 层 5 文件, 2026-08-05).

Mirrors tests/test_orm_b5_task1_row_shape_e2e.py / test_scheduled_master_
row_shape_e2e.py: every purely compile-level assertion in this batch's
coverage would pass whether a statement selects column-level (correct) or
entity-level (the B4 Critical bug class — ``select(Entity)``/
``select(*Entity.__table__.c)`` mixed up maps each row to ONE key (the
entity name) instead of one key per column, and every consumer's
``row["col"]``/``row.get("col")`` silently returns None/raises instead).
Compile-level assertions cannot tell the two apart because they render
near-identical SQL text; only a REAL materialized ``Result`` exposes the
difference.

This file proves, for each take-row→consume statement this batch touches,
with a genuine ``aiosqlite``-backed ``AsyncSession``:

  1. the REAL production statement (imported from the module, never locally
     reconstructed) yields a RowMapping whose keys/values match exactly what
     the production consumer reads, and
  2. (negative control, highest-risk site only —
     ``agent_runs_sweeper._agents_budget_scan_stmt``) the entity-level
     alternative, executed against the identical table/row, produces the
     wrong shape — proving the positive test is actually sensitive to the
     bug class, not just re-confirming whatever the code already does. The
     other three sites in this batch select individual named columns (never
     ``select(Model)``/``select(*Model.__table__.c)`` ambiguity), so a
     negative control would just be re-deriving the same assertion; they get
     a positive real-engine round trip proving the JOIN/DISTINCT/CASE
     mechanics instead.

Covers:
  - app.workflows.agent_runs_sweeper._agents_budget_scan_stmt (ai_agents)
  - app.workflows.temp_resource_sweeper._temp_folder_scopes_stmt (folders
    LEFT JOIN teams)
  - app.workflows.backfill_normalize_personal_project_team_ids.
    _misstamped_personal_projects_stmt (projects JOIN teams)
  - app.workflows.autopilot_sweep._eligible_projects_stmt (projects JOIN
    project_stage_nodes, JSONB ->> text predicate)
  - app.workflows.scheduled_quotas._grant_daily_free_points_stmt /
    _reclaim_daily_free_points_stmt: these wrap POSTGRES STORED FUNCTIONS as
    table-valued expressions, so they cannot round-trip through aiosqlite (no
    such function exists in SQLite) — covered instead by a compile-level
    assertion on ``stmt.selected_columns.keys()``, which is exactly what
    determines the ``.mappings()`` row shape for a table-valued FROM clause
    (no entity-vs-column ambiguity is possible here: the columns are named
    directly in ``.table_valued(...)``).

Real model ``__table__`` objects drive both DDL and INSERT (real bind/result
processors); only the DDL is hand-rolled with SQLite-native column types
because the real models declare Postgres-only DDL (JSONB, dialect UUID,
BigInteger identity, ``now()``/``generate_snowflake_id()`` server defaults)
that SQLite's DDL compiler cannot render — ``schema_translate_map`` strips
the compiled statement's ``public.`` prefix so the unqualified SQLite table
resolves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AiAgents, Folders, Projects, ProjectStageNodes, Teams
from app.workflows.agent_runs_sweeper import _agents_budget_scan_stmt
from app.workflows.autopilot_sweep import _eligible_projects_stmt
from app.workflows.backfill_normalize_personal_project_team_ids import (
    _misstamped_personal_projects_stmt,
)
from app.workflows.scheduled_quotas import (
    _grant_daily_free_points_stmt,
    _reclaim_daily_free_points_stmt,
)
from app.workflows.temp_resource_sweeper import _temp_folder_scopes_stmt

_UID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ── agent_runs_sweeper.py — ai_agents column-level budget scan ─────────────

_AI_AGENTS_DDL = """
CREATE TABLE ai_agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    current_version INTEGER,
    fallback_models TEXT,
    persistent BOOLEAN,
    description TEXT,
    model TEXT,
    temperature NUMERIC,
    max_tokens INTEGER,
    team_id INTEGER,
    project_id INTEGER,
    created_by TEXT,
    enabled BOOLEAN,
    sort_order INTEGER,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    slug TEXT,
    identity_md TEXT,
    soul_md TEXT,
    agent_md TEXT,
    is_system_preset BOOLEAN,
    capability_profile TEXT,
    user_id TEXT,
    icon TEXT,
    monthly_token_budget INTEGER,
    monthly_cost_cents_budget NUMERIC,
    paused_reason TEXT,
    budget_per_run_cents NUMERIC,
    memory_injection_top_n INTEGER,
    seed_hash TEXT,
    timeout_sec INTEGER,
    max_concurrent_runs INTEGER,
    agent_group TEXT
)
"""


async def _make_ai_agents_engine_with_one_agent():
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_AI_AGENTS_DDL)
        await conn.execute(
            insert(AiAgents.__table__).values(
                id=_AGENT_ID,
                name="script_ai",
                current_version=1,
                # fallback_models omitted (NULL): its Postgres-only ARRAY
                # type has no SQLite bind processor for a Python list — not
                # relevant to this test's row-shape assertions.
                persistent=False,
                capability_profile={},
                monthly_token_budget=1000,
                monthly_cost_cents_budget=Decimal("50.00"),
                paused_reason=None,
            )
        )
    return engine


@pytest.mark.asyncio
async def test_agents_budget_scan_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_agents_budget_scan_stmt, imported —
    not reconstructed here) round-tripped through a genuine Result gives a
    column-keyed RowMapping matching recompute_monthly_budgets_step's
    ``agent["id"]``/``agent.get("monthly_token_budget")``/``agent.get(
    "paused_reason")`` reads."""
    engine = await _make_ai_agents_engine_with_one_agent()
    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(_agents_budget_scan_stmt([_AGENT_ID])))
                .mappings()
                .all()
            )
    finally:
        await engine.dispose()

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == _AGENT_ID
    assert row.get("monthly_token_budget") == 1000
    assert Decimal(str(row.get("monthly_cost_cents_budget"))) == Decimal("50.00")
    assert row.get("paused_reason") is None


@pytest.mark.asyncio
async def test_agents_budget_scan_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: the KNOWN-BAD ``select(AiAgents)`` form, executed
    against the exact same real table/row, must produce the wrong shape —
    proving the test above actually distinguishes correct from broken."""
    engine = await _make_ai_agents_engine_with_one_agent()
    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = select(AiAgents).execution_options(
            schema_translate_map={"public": None}
        )
        async with sessionmaker() as session:
            bad_rows = (await session.execute(bad_stmt)).mappings().all()
    finally:
        await engine.dispose()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert list(bad_row.keys()) == ["AiAgents"]  # entity-keyed, not column-keyed
    assert bad_row.get("monthly_token_budget") is None  # the consumer's read breaks
    with pytest.raises(KeyError):
        bad_row["id"]


# ── temp_resource_sweeper.py — folders LEFT JOIN teams, CASE + DISTINCT ────

_FOLDERS_DDL = """
CREATE TABLE folders (
    name TEXT, scope_id INTEGER, created_by TEXT, sort_order INTEGER,
    is_system BOOLEAN, visibility TEXT, is_trashed BOOLEAN,
    created_at TIMESTAMP, updated_at TIMESTAMP, id INTEGER PRIMARY KEY,
    icon TEXT, color TEXT, trashed_at TIMESTAMP, parent_id INTEGER,
    library_id INTEGER, is_smart BOOLEAN, smart_rules TEXT
)
"""
_TEAMS_DDL = """
CREATE TABLE teams (
    name TEXT, owner_id TEXT, invite_code TEXT, id INTEGER PRIMARY KEY,
    settings_json TEXT, kind TEXT, created_at TIMESTAMP, enabled_modules TEXT
)
"""


@pytest.mark.asyncio
async def test_temp_folder_scopes_stmt_yields_column_keyed_rows_personal_and_team():
    """The REAL production statement (_temp_folder_scopes_stmt) round-tripped
    through a genuine Result gives column-keyed RowMappings matching
    ``_iter_scopes``'s ``row["scope_type"]``/``row["scope_id"]`` reads — one
    row per distinct (personal-team, collaborative-team, orphan-scope LEFT
    JOIN miss, and excluded-trashed) case. Each of teams 2/3/999 owns exactly
    one folder so the WHERE/JOIN conditions are independently discriminative
    (fix-round self-verification: the ``is_trashed`` filter was flipped
    locally, confirmed this test goes red — team 3 would otherwise leak in —
    then restored; see PR description)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_FOLDERS_DDL)
        await conn.exec_driver_sql(_TEAMS_DDL)
        await conn.execute(
            insert(Teams.__table__).values(
                id=1,
                name="Personal",
                owner_id=_UID,
                invite_code="p1",
                settings_json="{}",
                kind="personal",
            )
        )
        await conn.execute(
            insert(Teams.__table__).values(
                id=2,
                name="Collab",
                owner_id=_UID,
                invite_code="c1",
                settings_json="{}",
                kind="collaborative",
            )
        )
        # team 3 owns ONLY a trashed folder — a leaked row here can only come
        # from the is_trashed filter being dropped, never from team 2's row.
        await conn.execute(
            insert(Teams.__table__).values(
                id=3,
                name="Collab (trashed-only)",
                owner_id=_UID,
                invite_code="c2",
                settings_json="{}",
                kind="collaborative",
            )
        )
        await conn.execute(
            insert(Folders.__table__).values(
                id=10,
                name="temp",
                scope_id=1,
                created_by=_UID,
                sort_order=0,
                is_system=True,
                visibility="inherited",
                is_trashed=False,
            )
        )
        await conn.execute(
            insert(Folders.__table__).values(
                id=11,
                name="temp",
                scope_id=2,
                created_by=_UID,
                sort_order=0,
                is_system=True,
                visibility="inherited",
                is_trashed=False,
            )
        )
        # A trashed temp folder must be excluded by the WHERE clause.
        await conn.execute(
            insert(Folders.__table__).values(
                id=12,
                name="temp",
                scope_id=3,
                created_by=_UID,
                sort_order=0,
                is_system=True,
                visibility="inherited",
                is_trashed=True,
            )
        )
        # Orphan scope (no matching team row) — the LEFT JOIN must still
        # yield the row (scope_type falls back to 'team' via the CASE), not
        # silently drop it the way an INNER JOIN would.
        await conn.execute(
            insert(Folders.__table__).values(
                id=13,
                name="temp",
                scope_id=999,
                created_by=_UID,
                sort_order=0,
                is_system=True,
                visibility="inherited",
                is_trashed=False,
            )
        )

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (await session.execute(_temp_folder_scopes_stmt())).mappings().all()
    finally:
        await engine.dispose()

    got = {(str(r["scope_type"]), str(r["scope_id"])) for r in rows}
    assert got == {("personal", "1"), ("team", "2"), ("team", "999")}


# ── backfill_normalize_personal_project_team_ids.py — projects JOIN teams ──

_PROJECTS_DDL = """
CREATE TABLE projects (
    name TEXT, owner_id TEXT, project_type TEXT, is_starred BOOLEAN,
    created_at TIMESTAMP, updated_at TIMESTAMP, visibility TEXT,
    id INTEGER PRIMARY KEY, description TEXT, project_group TEXT,
    workflow_id TEXT, team_id INTEGER, announcement TEXT, color_label TEXT,
    current_canvas_id INTEGER, current_node_id INTEGER, topic_id INTEGER,
    archived_at TIMESTAMP, autopilot_enabled BOOLEAN
)
"""


@pytest.mark.asyncio
async def test_misstamped_personal_projects_stmt_yields_column_keyed_row():
    """The REAL production statement round-tripped through a genuine Result
    gives a column-keyed RowMapping (``row["project_id"]``) matching
    ``run_backfill``'s read. Fixture independently discriminates BOTH JOIN
    conditions (fix-round self-verification: each was flipped locally,
    confirmed this test goes red, then restored — see PR description):

      - project 1 (team 100: owner_a, personal)      → MATCH (mis-stamped)
      - project 2 (team 200: owner_a, collaborative)  → excluded by kind
      - project 4 (team 300: owner_b, personal)       → excluded by owner
      - project 3 (team_id NULL)                      → excluded by WHERE
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    owner_a = uuid.UUID("33333333-3333-3333-3333-333333333333")
    owner_b = uuid.UUID("44444444-4444-4444-4444-444444444444")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_TEAMS_DDL)
        await conn.exec_driver_sql(_PROJECTS_DDL)
        # owner_a's own personal team.
        await conn.execute(
            insert(Teams.__table__).values(
                id=100,
                name="Personal A",
                owner_id=owner_a,
                invite_code="pa",
                settings_json="{}",
                kind="personal",
            )
        )
        # owner_a's OWN collaborative team — owner matches, kind doesn't.
        await conn.execute(
            insert(Teams.__table__).values(
                id=200,
                name="Collab A",
                owner_id=owner_a,
                invite_code="ca",
                settings_json="{}",
                kind="collaborative",
            )
        )
        # owner_b's personal team — kind matches, owner doesn't.
        await conn.execute(
            insert(Teams.__table__).values(
                id=300,
                name="Personal B",
                owner_id=owner_b,
                invite_code="pb",
                settings_json="{}",
                kind="personal",
            )
        )
        now = datetime.now(timezone.utc)
        # Mis-stamped: project 1 is owner_a's, stamped with owner_a's OWN
        # personal team → must be selected.
        await conn.execute(
            insert(Projects.__table__).values(
                id=1,
                name="Project A",
                owner_id=owner_a,
                project_type="personal",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                team_id=100,
                autopilot_enabled=False,
            )
        )
        # Legitimately collaborative: owner matches team 200, but its kind is
        # 'collaborative' → must NOT be selected (tests the kind filter).
        await conn.execute(
            insert(Projects.__table__).values(
                id=2,
                name="Project B",
                owner_id=owner_a,
                project_type="external",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                team_id=200,
                autopilot_enabled=False,
            )
        )
        # NULL team_id (already-personal convention) → excluded by WHERE.
        await conn.execute(
            insert(Projects.__table__).values(
                id=3,
                name="Project C",
                owner_id=owner_a,
                project_type="personal",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                team_id=None,
                autopilot_enabled=False,
            )
        )
        # Someone ELSE's personal team (team 300 is owner_b's): kind matches
        # but owner doesn't → must NOT be selected (tests the owner filter).
        await conn.execute(
            insert(Projects.__table__).values(
                id=4,
                name="Project D",
                owner_id=owner_a,
                project_type="personal",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                team_id=300,
                autopilot_enabled=False,
            )
        )

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(_misstamped_personal_projects_stmt(500)))
                .mappings()
                .all()
            )
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert rows[0]["project_id"] == 1


# ── autopilot_sweep.py — projects JOIN project_stage_nodes, JSONB ->> ──────

_PROJECT_STAGE_NODES_DDL = """
CREATE TABLE project_stage_nodes (
    id INTEGER PRIMARY KEY, project_id INTEGER, source_template_node_id INTEGER,
    legacy_stage_id INTEGER, name TEXT, sort_order INTEGER, parallel_group INTEGER,
    status TEXT, owner_user_id TEXT, owner_agent_id TEXT, planned_start DATE,
    planned_due DATE, review_required BOOLEAN, deliverable_required BOOLEAN,
    deliverable_label TEXT, skipped BOOLEAN, folder_id INTEGER,
    completion_policy TEXT, events TEXT, metadata TEXT, form_schema TEXT,
    form_data TEXT, brief TEXT, created_at TIMESTAMP, updated_at TIMESTAMP,
    episode_id INTEGER, surface TEXT
)
"""


@pytest.mark.asyncio
async def test_eligible_projects_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement round-tripped through a genuine Result
    gives a column-keyed RowMapping (``row["id"]``) — proves the JOIN +
    DISTINCT + single-explicit-column select mechanics, and that the status/
    skipped/JSONB predicates correctly exclude ineligible nodes. Fix-round
    self-verification: the ``autopilot_enabled`` filter was flipped locally
    (dropped), confirmed this test goes red (project 2 leaks in via its
    eligible node), then restored — see PR description.

    NOTE on the JSONB predicate: Postgres's ``jsonb ->> 'k'`` renders a JSON
    *boolean* as the TEXT ``'true'``/``'false'``; SQLite's json1 ``->>``
    renders a JSON boolean as the INTEGER 0/1 instead (verified empirically —
    there is no SQLite pragma to change this). Storing ``auto_start`` as the
    JSON *string* ``"true"`` sidesteps that divergence — both dialects

    NOTE on the JSONB predicate: Postgres's ``jsonb ->> 'k'`` renders a JSON
    *boolean* as the TEXT ``'true'``/``'false'``; SQLite's json1 ``->>``
    renders a JSON boolean as the INTEGER 0/1 instead (verified empirically —
    there is no SQLite pragma to change this). Storing ``auto_start`` as the
    JSON *string* ``"true"`` sidesteps that divergence — both dialects
    extract a JSON string via ``->>`` identically as the unquoted text — so
    this fixture isolates the mechanic under test (JOIN/DISTINCT/column
    shape) from the boolean-representation difference, which is a Postgres-
    only behavior already pinned by the compile-level assertion in
    tests/test_autopilot_sweep.py (``events ->> 'auto_start') = 'true'`` with
    no ``::boolean`` cast)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_PROJECTS_DDL)
        await conn.exec_driver_sql(_PROJECT_STAGE_NODES_DDL)
        now = datetime.now(timezone.utc)
        await conn.execute(
            insert(Projects.__table__).values(
                id=1,
                name="Eligible",
                owner_id=_UID,
                project_type="personal",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                autopilot_enabled=True,
            )
        )
        await conn.execute(
            insert(Projects.__table__).values(
                id=2,
                name="Autopilot off",
                owner_id=_UID,
                project_type="personal",
                is_starred=False,
                created_at=now,
                updated_at=now,
                visibility="inherited",
                autopilot_enabled=False,
            )
        )

        def _node(**overrides):
            base = dict(
                sort_order=0,
                status="pending",
                review_required=False,
                deliverable_required=False,
                skipped=False,
                completion_policy="all",
                events={"auto_start": "true"},
                metadata={},
                form_schema={},
                form_data={},
                brief="",
                created_at=now,
                updated_at=now,
            )
            base.update(overrides)
            return base

        await conn.execute(
            insert(ProjectStageNodes.__table__).values(
                id=10, project_id=1, name="eligible-node", **_node()
            )
        )
        await conn.execute(
            insert(ProjectStageNodes.__table__).values(
                id=11, project_id=2, name="autopilot-off-node", **_node()
            )
        )
        await conn.execute(
            insert(ProjectStageNodes.__table__).values(
                id=12, project_id=1, name="done-node", **_node(status="done")
            )
        )
        await conn.execute(
            insert(ProjectStageNodes.__table__).values(
                id=13,
                project_id=1,
                name="not-auto-start-node",
                **_node(events={"auto_start": "false"}),
            )
        )

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(_eligible_projects_stmt(200))).mappings().all()
            )
    finally:
        await engine.dispose()

    assert [r["id"] for r in rows] == [1]


# ── scheduled_quotas.py — table-valued stored-function selects ────────────
# These wrap real Postgres functions (grant_daily_free_points_batch /
# reclaim_daily_free_points_batch) as table-valued expressions — SQLite has
# no such functions to round-trip against, so the row-shape guarantee is
# pinned at compile time instead: ``.table_valued("granted", "skipped")``
# IS what determines the output column names (there's no select(Entity)
# ambiguity possible for a table-valued function call).


def test_grant_daily_free_points_stmt_selects_named_columns():
    stmt = _grant_daily_free_points_stmt(100, date(2026, 8, 5))
    assert list(stmt.selected_columns.keys()) == ["granted", "skipped"]
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "public.grant_daily_free_points_batch(100, '2026-08-05')" in sql


def test_reclaim_daily_free_points_stmt_selects_named_columns():
    stmt = _reclaim_daily_free_points_stmt(date(2026, 8, 4))
    assert list(stmt.selected_columns.keys()) == ["reclaimed_count", "total_reclaimed"]
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "public.reclaim_daily_free_points_batch('2026-08-04')" in sql
