"""DB-backed proof for the two reads the turn-done watermark is built on (3b §4).

WHY THIS FILE EXISTS
────────────────────
``AgentRunsRepository.last_transcript_seq`` and
``RunDeliverablesRepository.output_keys_for_run`` are new ORM statements, and
every unit test around their consumers stubs them outright — so without this
file **neither statement would ever have been executed by Postgres**, the same
gap ``tests/db/test_run_deliverables_repository_integration.py`` opens with.
Both are aggregates, which is exactly the shape a stub cannot vouch for:

  * ``select(func.max(TE.seq))`` on a run with NO rows returns one row holding
    NULL — ``scalar_one_or_none()`` must therefore hand back ``None``, not
    raise ``NoResultFound`` and not yield 0. The whole contract is that "no
    events" and "seq 0" are different answers (seq 0 would make the frontend
    drop the newest frame as the oldest), and only the server settles which
    one this query gives.
  * ``GROUP BY kind, ref_id ORDER BY MIN(seq)`` has to actually collapse two
    versions of one object into ONE key and order the groups by first
    registration. A stubbed session returns whatever list the test wrote.
  * ``run_deliverables.seq`` is NULLABLE (the registry inserts the row before
    the event exists and back-fills via ``set_seq``), so ``MIN(seq)`` can be
    NULL inside a group. Postgres sorts NULLS LAST on ASC — the position an
    unstamped registration should hold — and no stub can confirm that.
  * ``ref_id`` must leave as ``str``: these keys go onto the WS ``done`` frame
    the browser matches its lineage cache against.
  * Both must be scoped to their run. Leaking across runs would put another
    issue's outputs on this issue's frame.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark), same as its neighbours:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_run_seq_and_output_keys_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — turn-watermark tests need a DB.",
)


def _uniq(prefix: str) -> str:
    """The 462 unique index is global across run_deliverables, so fixed ref_ids
    would make the cases interfere."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore + dispose."""
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
async def fx(pg) -> Dict[str, Any]:
    """Two ``agent_runs`` rows. The second one is what proves both reads are
    scoped to their run rather than answering for the whole table."""
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        _uniq("watermark-test-agent"),
    )

    async def _mk_run() -> int:
        return await pg.fetchval(
            """
            INSERT INTO public.agent_runs (agent_id, user_id, status, trigger)
            VALUES ($1, $2, 'running', 'test')
            RETURNING id
            """,
            agent_id,
            uuid.uuid4(),
        )

    run_a = await _mk_run()
    run_b = await _mk_run()
    try:
        yield {"run_a": run_a, "run_b": run_b, "agent": agent_id}
    finally:
        # agent_run_transcript_events / run_deliverables are ON DELETE CASCADE.
        await pg.execute(
            "DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])",
            [run_a, run_b],
        )
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


def _runs_repo():
    from app.repositories.agent_runs_repository import AgentRunsRepository

    return AgentRunsRepository()


def _deliverables_repo():
    from app.repositories.run_deliverables_repository import RunDeliverablesRepository

    return RunDeliverablesRepository()


async def _event(pg, run_id: int, seq: int, event_type: str = "step_end") -> None:
    await pg.execute(
        """
        INSERT INTO public.agent_run_transcript_events (run_id, seq, event_type)
        VALUES ($1, $2, $3)
        """,
        run_id,
        seq,
        event_type,
    )


# ---------------------------------------------------------------------------
# last_transcript_seq
# ---------------------------------------------------------------------------


@_skip
async def test_a_run_with_no_events_reads_as_none_not_zero(orm_dsn, fx):
    """``MAX()`` over zero rows is one row holding NULL. If this raised, or if
    it answered 0, every frame for a run that has not recorded anything yet
    would claim to be at the very bottom of the stream."""
    got = await _runs_repo().last_transcript_seq(fx["run_a"])
    assert got is None
    assert got is not False and got != 0  # "unknown" must not read as a number


@_skip
async def test_the_watermark_is_the_max_and_stays_inside_its_run(orm_dsn, fx, pg):
    repo = _runs_repo()
    for seq in (1, 2, 3):
        await _event(pg, fx["run_a"], seq)
    # A busier neighbour — the watermark must not follow it.
    for seq in (1, 2, 3, 4, 5, 6, 7):
        await _event(pg, fx["run_b"], seq)

    assert await repo.last_transcript_seq(fx["run_a"]) == 3
    assert await repo.last_transcript_seq(fx["run_b"]) == 7

    await _event(pg, fx["run_a"], 4, "deliverable")
    assert await repo.last_transcript_seq(fx["run_a"]) == 4


# ---------------------------------------------------------------------------
# output_keys_for_run
# ---------------------------------------------------------------------------


async def _register(repo, *, run_id, kind, ref_id, version, seq):
    row = await repo.insert_version(
        run_id=run_id,
        kind=kind,
        ref_id=ref_id,
        version=version,
        parent_version=version - 1 or None,
        title=f"v{version}",
        model=None,
        cost_cents=None,
        turn=1,
        step=version,
    )
    if seq is not None:
        await repo.set_seq(row_id=row["id"], seq=seq)
    return row


@_skip
async def test_a_run_that_registered_nothing_has_no_output_keys(orm_dsn, fx):
    assert await _deliverables_repo().output_keys_for_run(fx["run_a"]) == []


@_skip
async def test_the_keys_are_distinct_per_object_and_ordered_by_first_seq(
    orm_dsn, fx, pg
):
    """Two versions of one object are ONE key: the frontend invalidates its
    lineage cache per object, not per version. Order is first registration.

    The shot is registered FIRST and sorts LAST alphabetically ("script_scene"
    < "script_shot"), so this ordering is only satisfiable by ``MIN(seq)`` —
    a plain ``ORDER BY kind`` would give the reverse and this test would say so.
    """
    repo = _deliverables_repo()
    shot = _uniq("shot")
    scene = _uniq("scene")
    try:
        await _register(
            repo, run_id=fx["run_a"], kind="script_shot", ref_id=shot, version=1, seq=3
        )
        await _register(
            repo,
            run_id=fx["run_a"],
            kind="script_scene",
            ref_id=scene,
            version=1,
            seq=5,
        )
        # A second version of the FIRST object, registered last. Its group must
        # keep the FIRST seq (3), not slide to the end on the newer one.
        await _register(
            repo, run_id=fx["run_a"], kind="script_shot", ref_id=shot, version=2, seq=9
        )

        keys = await repo.output_keys_for_run(fx["run_a"])

        assert keys == [
            {"kind": "script_shot", "ref_id": shot},
            {"kind": "script_scene", "ref_id": scene},
        ]
        # The frame is JSON: ref_id must already be a string.
        assert all(isinstance(k["ref_id"], str) for k in keys)
    finally:
        await pg.execute(
            "DELETE FROM public.run_deliverables WHERE ref_id = ANY($1::text[])",
            [shot, scene],
        )


@_skip
async def test_an_unstamped_registration_still_appears_and_sorts_last(orm_dsn, fx, pg):
    """``seq`` is filled by a second UPDATE after the event lands, so a row can
    legitimately have none. Dropping it would silently lose an output from the
    frame; Postgres sorts the NULL group last, which is where "not on the
    transcript yet" belongs."""
    repo = _deliverables_repo()
    stamped = _uniq("shot")
    unstamped = _uniq("gm")
    try:
        await _register(
            repo,
            run_id=fx["run_a"],
            kind="script_shot",
            ref_id=stamped,
            version=1,
            seq=7,
        )
        await _register(
            repo,
            run_id=fx["run_a"],
            kind="generated_media",
            ref_id=unstamped,
            version=1,
            seq=None,
        )

        keys = await repo.output_keys_for_run(fx["run_a"])

        assert keys == [
            {"kind": "script_shot", "ref_id": stamped},
            {"kind": "generated_media", "ref_id": unstamped},
        ]
    finally:
        await pg.execute(
            "DELETE FROM public.run_deliverables WHERE ref_id = ANY($1::text[])",
            [stamped, unstamped],
        )


@_skip
async def test_the_keys_do_not_leak_across_runs(orm_dsn, fx, pg):
    """Another run's outputs on this frame would invalidate the wrong caches."""
    repo = _deliverables_repo()
    mine = _uniq("shot")
    theirs = _uniq("shot")
    try:
        await _register(
            repo, run_id=fx["run_a"], kind="script_shot", ref_id=mine, version=1, seq=1
        )
        await _register(
            repo,
            run_id=fx["run_b"],
            kind="script_shot",
            ref_id=theirs,
            version=1,
            seq=1,
        )

        assert await repo.output_keys_for_run(fx["run_a"]) == [
            {"kind": "script_shot", "ref_id": mine}
        ]
        assert await repo.output_keys_for_run(fx["run_b"]) == [
            {"kind": "script_shot", "ref_id": theirs}
        ]
    finally:
        await pg.execute(
            "DELETE FROM public.run_deliverables WHERE ref_id = ANY($1::text[])",
            [mine, theirs],
        )
