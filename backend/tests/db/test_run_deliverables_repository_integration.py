"""DB-backed integration tests for ``RunDeliverablesRepository`` (mig 453/462).

WHY THIS FILE EXISTS
────────────────────
The repository is pure ORM statements and every unit test around it stubs the
session — so until this file landed **not one of those statements had ever been
executed by Postgres**, while Task 3's endpoints consume them directly. The same
shape of gap ``tests/db/test_assets_repository_integration.py`` opens with, and
the same one CLAUDE.md's "读正常 ≠ 服务正常" warns about. Specifically:

  * ``insert(RunDeliverables).returning(RunDeliverables)`` fed to
    ``scalar_one()`` yielding a MAPPED ENTITY (not a Row) is an ORM-dialect
    behaviour, not something SQLAlchemy guarantees by construction (case 1).
  * ``_row`` reads every column off that entity — including the server-side
    ``DEFAULT generate_snowflake_id()`` id and ``DEFAULT now()`` created_at,
    which only exist after the server has spoken (case 1).
  * ``cost_cents NUMERIC(12,4)`` comes back as ``Decimal``; ``_row`` must turn
    it into a float or the row cannot be JSON-encoded by Task 3's endpoint
    (case 2).
  * BIGINT ``id`` / ``run_id`` must leave as STRINGS — a JSON number above 2^53
    loses precision in the browser (Snowflake discipline, case 2).
  * the 462 unique index ``run_deliverables_kind_ref_version_key`` must actually
    reject a duplicate ``(kind, ref_id, version)`` — the registry's whole
    concurrency story is "the index arbitrates" (case 4).
  * ``latest_version`` must read the MAX through
    ``idx_run_deliverables_ref_latest``, and must not leak across ``kind`` or
    ``ref_id`` (case 3).
  * the two JOINs onto ``agent_runs`` (``list_for_issue`` / ``lineage_for``)
    have to produce the ordering Task 3 paginates on, and ``lineage_for``'s
    multi-entity ``select(Model, Column, Column, Column)`` returns Rows that
    unpack — a different result shape from the single-entity selects above, and
    its OUTER join onto ``issues`` (Task 3b's ``issue_key`` / ``team_id``) must
    not drop the rows of a run that answers to no issue (cases 5, 6).

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixture setup and for the
assertions; the repository itself goes through ``app.db.session`` (SQLAlchemy
async engine), which the ``orm_dsn`` fixture repoints at the same DSN. Same
pattern as ``tests/db/test_assets_repository_integration.py``.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 453/462):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_run_deliverables_repository_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
agent_runs fixture with fresh ids and tears it down in a ``finally``, so the
cases are independent and the file is re-runnable against the same DB.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — run_deliverables integration tests "
        "need a DB."
    ),
)


def _uniq(prefix: str) -> str:
    """A ref_id no other run (or case) can collide with — the 462 unique index
    is global across the table, so fixed ids would make cases interfere."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine (app.db.session read/write scope) at the test DSN
    for the duration of a test, then restore + dispose so no other test inherits
    a stray engine."""
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
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """Two agent_runs rows on two different issues.

    ``run_deliverables.run_id`` FK-references ``public.agent_runs``, and issue
    attribution is resolved BY JOIN through that FK (the table stores no
    issue_id — spec §3), so a second run on a second issue is what proves
    ``list_for_issue`` actually filters rather than returning everything.

    Both FKs above ``agent_runs`` are REAL and had to be satisfied — the first
    draft of this fixture invented a uuid for ``agent_id`` and a bigint for
    ``issue_id`` and was rejected on both counts by
    ``agent_runs_agent_id_fkey`` / ``agent_runs_issue_id_fkey``; the issue rows
    then had to name a creator (``issues_creator_required``). That is the file
    earning its keep before it asserted anything: the unit tests, whose session
    is stubbed, are structurally unable to notice any of the three.

    Only the NOT NULL columns each table declares are supplied.
    """
    user_id = uuid.uuid4()

    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        _uniq("deliverables-test-agent"),
    )

    async def _mk_issue(n: int) -> int:
        return await pg.fetchval(
            """
            INSERT INTO public.issues
                (issue_number, identifier, title, created_by_agent_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            n,
            _uniq("DLV"),
            "deliverables integration fixture",
            agent_id,
        )

    async def _mk_run(issue_id: int) -> int:
        return await pg.fetchval(
            """
            INSERT INTO public.agent_runs
                (agent_id, user_id, status, trigger, issue_id)
            VALUES ($1, $2, 'completed', 'test', $3)
            RETURNING id
            """,
            agent_id,
            user_id,
            issue_id,
        )

    issue_a = await _mk_issue(990_001)
    issue_b = await _mk_issue(990_002)
    run_a = await _mk_run(issue_a)
    run_b = await _mk_run(issue_b)

    async def _key(issue_id: int) -> str:
        return await pg.fetchval(
            "SELECT identifier FROM public.issues WHERE id = $1", issue_id
        )

    try:
        yield {
            "run_a": run_a,
            "run_b": run_b,
            "issue_a": issue_a,
            "issue_b": issue_b,
            # The identifiers Postgres stored, for the deep-link join (Task 3b).
            "key_a": await _key(issue_a),
            "key_b": await _key(issue_b),
        }
    finally:
        # ON DELETE CASCADE takes the deliverables with the runs; agent_runs
        # .issue_id is ON DELETE SET NULL, so the runs go first either way.
        await pg.execute(
            "DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])",
            [run_a, run_b],
        )
        await pg.execute(
            "DELETE FROM public.issues WHERE id = ANY($1::bigint[])",
            [issue_a, issue_b],
        )
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


def _repo():
    from app.repositories.run_deliverables_repository import RunDeliverablesRepository

    return RunDeliverablesRepository()


# ---------------------------------------------------------------------------
# case 1 — the INSERT ... RETURNING entity really round-trips
# ---------------------------------------------------------------------------


@_skip
async def test_insert_version_returns_the_server_built_row(orm_dsn, fx, pg):
    ref = _uniq("shot")
    row = await _repo().insert_version(
        run_id=fx["run_a"],
        kind="script_shot",
        ref_id=ref,
        version=1,
        parent_version=None,
        title="S3 · Shot 1 · MS",
        model=None,
        cost_cents=None,
        turn=1,
        step=3,
    )

    # Server-side defaults only exist after Postgres has spoken.
    assert row["id"] and row["created_at"]
    assert row["kind"] == "script_shot" and row["ref_id"] == ref
    assert (row["version"], row["parent_version"]) == (1, None)
    assert (row["turn"], row["step"]) == (1, 3)
    # BIGINTs leave as strings (Snowflake precision discipline).
    assert isinstance(row["id"], str) and isinstance(row["run_id"], str)
    assert row["run_id"] == str(fx["run_a"])

    stored = await pg.fetchrow(
        "SELECT run_id, kind, version FROM public.run_deliverables WHERE id = $1",
        int(row["id"]),
    )
    assert stored["run_id"] == fx["run_a"] and stored["version"] == 1


@_skip
async def test_a_numeric_cost_comes_back_as_a_json_safe_float(orm_dsn, fx):
    """``NUMERIC(12,4)`` is a ``Decimal`` on the way out — Task 3's endpoint
    cannot JSON-encode that."""
    row = await _repo().insert_version(
        run_id=fx["run_a"],
        kind="generated_media",
        ref_id=_uniq("gm"),
        version=1,
        parent_version=None,
        title="A cafe at dusk",
        model="gpt-image-2.5",
        cost_cents=0.1234,
        turn=1,
        step=2,
    )
    assert isinstance(row["cost_cents"], float)
    assert row["cost_cents"] == pytest.approx(0.1234)


# ---------------------------------------------------------------------------
# case 3 — latest_version
# ---------------------------------------------------------------------------


@_skip
async def test_latest_version_reads_the_max_and_does_not_leak_across_refs(orm_dsn, fx):
    repo = _repo()
    ref = _uniq("shot")
    other = _uniq("shot")

    assert await repo.latest_version(kind="script_shot", ref_id=ref) is None

    for version in (1, 2, 3):
        await repo.insert_version(
            run_id=fx["run_a"],
            kind="script_shot",
            ref_id=ref,
            version=version,
            parent_version=version - 1 or None,
            title=f"v{version}",
            model=None,
            cost_cents=None,
            turn=1,
            step=version,
        )

    assert await repo.latest_version(kind="script_shot", ref_id=ref) == 3
    # Same ref_id, different kind → a different object entirely.
    assert await repo.latest_version(kind="script_scene", ref_id=ref) is None
    assert await repo.latest_version(kind="script_shot", ref_id=other) is None


# ---------------------------------------------------------------------------
# case 4 — the 462 unique index is the concurrency arbiter
# ---------------------------------------------------------------------------


@_skip
async def test_the_unique_index_rejects_a_duplicate_version(orm_dsn, fx):
    """The registry's whole race story is "one loses and recomputes". That only
    holds if Postgres actually refuses the second writer."""
    from sqlalchemy.exc import IntegrityError

    repo = _repo()
    ref = _uniq("shot")
    common = dict(
        kind="script_shot",
        ref_id=ref,
        version=1,
        parent_version=None,
        title="t",
        model=None,
        cost_cents=None,
        turn=1,
        step=1,
    )
    await repo.insert_version(run_id=fx["run_a"], **common)

    # A DIFFERENT run claiming the same (kind, ref_id, version) — exactly the
    # two-runs-one-object race the index exists to settle.
    with pytest.raises(IntegrityError):
        await repo.insert_version(run_id=fx["run_b"], **common)


@_skip
async def test_the_registry_recovers_from_that_conflict_on_a_real_db(orm_dsn, fx):
    """End-to-end over the real index: the loser recomputes and lands v2.

    The unit test proves the retry logic with a stubbed repo; this one proves
    the exception it retries on is the one Postgres really raises.
    """
    from app.services.deliverables.registry import _insert_next_version

    repo = _repo()
    ref = _uniq("shot")
    base = dict(
        run_id=fx["run_a"],
        title="t",
        model=None,
        cost_cents=None,
        turn=1,
        step=1,
    )
    first = await _insert_next_version(repo, kind="script_shot", ref_id=ref, **base)
    second = await _insert_next_version(repo, kind="script_shot", ref_id=ref, **base)

    assert (first.version, first.parent_version) == (1, None)
    assert (second.version, second.parent_version) == (2, 1)


# ---------------------------------------------------------------------------
# case 4b — set_seq (三期 3a Task 8a): the only writer of run_deliverables.seq
# ---------------------------------------------------------------------------


@_skip
async def test_set_seq_writes_the_column_and_touches_nothing_else(orm_dsn, fx, pg):
    """The registry inserts the row BEFORE the event exists, so ``seq`` can
    only be filled by this UPDATE afterwards. Until Task 8a there was no
    writer at all and the documented wire field was always null."""
    repo = _repo()
    ref = _uniq("shot")
    row = await repo.insert_version(
        run_id=fx["run_a"],
        kind="script_shot",
        ref_id=ref,
        version=1,
        parent_version=None,
        title="S3 · Shot 1 · MS",
        model=None,
        cost_cents=None,
        turn=1,
        step=3,
    )
    assert row["seq"] is None  # nothing knows the seq at insert time

    await repo.set_seq(row_id=row["id"], seq=4)

    stored = await pg.fetchrow(
        "SELECT seq, version, title, turn, step FROM public.run_deliverables "
        "WHERE id = $1",
        int(row["id"]),
    )
    assert stored["seq"] == 4
    # The back-fill must not disturb the row it points into.
    assert (stored["version"], stored["turn"], stored["step"]) == (1, 1, 3)
    assert stored["title"] == "S3 · Shot 1 · MS"
    # And it is visible to the read path the wire contract is served from.
    chain = await repo.lineage_for(kind="script_shot", ref_id=ref)
    assert [v["seq"] for v in chain] == [4]


@_skip
async def test_set_seq_on_a_row_that_is_gone_is_a_no_op_not_an_error(orm_dsn, fx):
    """The registry treats a failed stamp as a WARNING; an UPDATE matching no
    row must therefore be silent rather than raise."""
    await _repo().set_seq(row_id=1, seq=9)


# ---------------------------------------------------------------------------
# cases 5, 6 — the two JOINs onto agent_runs
# ---------------------------------------------------------------------------


@_skip
async def test_list_for_issue_joins_through_the_run_and_filters(orm_dsn, fx):
    repo = _repo()
    ref_shot = _uniq("shot")
    ref_scene = _uniq("scene")
    ref_other = _uniq("shot")

    for version in (1, 2):
        await repo.insert_version(
            run_id=fx["run_a"],
            kind="script_shot",
            ref_id=ref_shot,
            version=version,
            parent_version=version - 1 or None,
            title=f"shot v{version}",
            model=None,
            cost_cents=None,
            turn=1,
            step=version,
        )
    await repo.insert_version(
        run_id=fx["run_a"],
        kind="script_scene",
        ref_id=ref_scene,
        version=1,
        parent_version=None,
        title="scene",
        model=None,
        cost_cents=None,
        turn=1,
        step=1,
    )
    # On the OTHER issue — must not appear.
    await repo.insert_version(
        run_id=fx["run_b"],
        kind="script_shot",
        ref_id=ref_other,
        version=1,
        parent_version=None,
        title="elsewhere",
        model=None,
        cost_cents=None,
        turn=1,
        step=1,
    )

    rows = await repo.list_for_issue(fx["issue_a"])

    assert [r["ref_id"] for r in rows].count(ref_other) == 0
    assert len(rows) == 3
    # ORDER BY kind, ref_id, version DESC — newest version of each object first.
    scene_rows = [r for r in rows if r["kind"] == "script_scene"]
    shot_rows = [r for r in rows if r["kind"] == "script_shot"]
    assert rows.index(scene_rows[0]) < rows.index(shot_rows[0])  # scene < shot
    assert [r["version"] for r in shot_rows] == [2, 1]


@_skip
async def test_lineage_for_returns_the_chain_newest_first_with_its_issue(orm_dsn, fx):
    """``select(Model, Column, …)`` returns Rows that unpack — a different
    result shape from every single-entity select above."""
    repo = _repo()
    ref = _uniq("gm")

    await repo.insert_version(
        run_id=fx["run_a"],
        kind="generated_media",
        ref_id=ref,
        version=1,
        parent_version=None,
        title="v1",
        model="m",
        cost_cents=None,
        turn=1,
        step=1,
    )
    # v2 produced by a run on a DIFFERENT issue — the lineage page's whole
    # point is saying which piece of work produced which version.
    await repo.insert_version(
        run_id=fx["run_b"],
        kind="generated_media",
        ref_id=ref,
        version=2,
        parent_version=1,
        title="v2",
        model="m",
        cost_cents=None,
        turn=1,
        step=1,
    )

    chain = await repo.lineage_for(kind="generated_media", ref_id=ref)

    assert [r["version"] for r in chain] == [2, 1]
    assert chain[0]["issue_id"] == str(fx["issue_b"])
    assert chain[1]["issue_id"] == str(fx["issue_a"])
    assert chain[0]["parent_version"] == 1 and chain[1]["parent_version"] is None
    # Task 3b: the outer join onto issues carries the KEY the frontend route
    # needs. Per row, because two versions of one object can come from runs on
    # two different issues — exactly the case above.
    assert chain[0]["issue_key"] == fx["key_b"]
    assert chain[1]["issue_key"] == fx["key_a"]
    # These fixture issues have no team, so the link is honestly unbuildable.
    assert chain[0]["team_id"] is None


@_skip
async def test_lineage_for_keeps_a_run_that_answers_to_no_issue(orm_dsn, fx, pg):
    """The issues join is OUTER on purpose. An inner join would make a canvas
    or chat lane run's whole version chain vanish — a 404 ``not_registered`` on
    an object that IS registered, which is the worst answer of the three."""
    repo = _repo()
    ref = _uniq("gm")
    await pg.execute(
        "UPDATE public.agent_runs SET issue_id = NULL WHERE id = $1", fx["run_a"]
    )
    await repo.insert_version(
        run_id=fx["run_a"],
        kind="generated_media",
        ref_id=ref,
        version=1,
        parent_version=None,
        title="orphan",
        model=None,
        cost_cents=None,
        turn=1,
        step=1,
    )

    chain = await repo.lineage_for(kind="generated_media", ref_id=ref)

    assert [r["version"] for r in chain] == [1]
    assert chain[0]["issue_id"] is None
    assert chain[0]["issue_key"] is None and chain[0]["team_id"] is None


@_skip
async def test_the_reads_are_empty_not_an_error_when_nothing_was_registered(
    orm_dsn, fx
):
    """「没登记」必须读成空，不是异常——血缘页对一个没被 agent 碰过的对象
    是正常访问，不是错误。"""
    repo = _repo()
    assert await repo.list_for_issue(fx["issue_a"]) == []
    assert await repo.lineage_for(kind="script_shot", ref_id=_uniq("nope")) == []
