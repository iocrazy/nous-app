"""mig 479 against a real Postgres: the three backfill tiers, and the child
runs that never got an ``issue_id``.

Why this has to run on a live database
======================================
Every claim the migration makes is a claim about **Postgres behaviour**, and a
stubbed session nods at all of them:

* Tier 1 reads ``metadata_json->'cost'->>'own_cents'`` and adds it to
  ``media_cents``. That the jsonb path yields a castable numeric — and that
  ``by_child`` is NOT swept in with it — is the server's answer, not ours.
* Tier 2 is gated on ``NOT EXISTS (… WHERE c.parent_run_id = a.id)``. A fake
  session applies no predicate at all, so a wrong one still reports success.
* Tier 3 is the absence of a write. "The row stayed NULL" can only be observed
  where the other two tiers actually ran.
* ``metadata_json ? 'cost'`` is the jsonb containment operator; whether a row
  carrying ``{"cost": …}`` lands in tier 1 rather than tier 2 decides whether
  the old (descendant-contaminated) column gets copied into the new one.

The migration body is replayed **from the file**, never retyped here — a copy
would let the file drift away from what this test proves.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_479_own_cost_backfill.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import decimal
import os
import pathlib
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 479 needs a real database.",
)

#: backend/tests/db/ → repo root → the migration under test.
_MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "479_agent_runs_own_cost_cents.sql"
)


def _body() -> str:
    """The migration with its ``--`` comment lines removed.

    Stripping them keeps the NOTICE-only DO block readable in the test output
    and costs nothing: the file has no ``--`` inside the ``DO $$`` block or
    inside any COMMENT literal, so no statement loses a line.

    Fed to ``conn.execute`` with **no parameters** on purpose. asyncpg only
    reaches for the extended protocol (one statement per call, ``?`` read as a
    placeholder) when arguments are passed; argument-free it uses the simple
    protocol, which takes the whole multi-statement file — ``DO $$ … $$`` and
    the ``metadata_json ? 'cost'`` operator included.
    """
    return "\n".join(
        line
        for line in _MIGRATION_SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


@pytest.fixture
async def conn():
    """A connection whose writes are always rolled back.

    The migration's UPDATEs sweep the whole table, so rows left behind by one
    case would be inputs to the next.
    """
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _mk_agent(conn) -> uuid.UUID:
    """``agent_id`` is NOT NULL and FKs to ``ai_agents``; ``name`` is the only
    column that table demands."""
    return await conn.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"own-cost-{uuid.uuid4().hex[:8]}",
    )


async def _mk_issue(conn, agent: uuid.UUID) -> int:
    """``issues`` wants issue_number / identifier / title, plus one creator
    (``issues_creator_required``). ``identifier`` is globally unique."""
    suffix = uuid.uuid4().hex[:8]
    return await conn.fetchval(
        "INSERT INTO public.issues "
        "  (issue_number, identifier, title, created_by_agent_id) "
        "VALUES ($1, $2, 'Own cost backfill', $3) RETURNING id",
        int(uuid.uuid4().int % 1_000_000),
        f"OWN-{suffix}",
        agent,
    )


async def _mk_user(conn) -> uuid.UUID:
    """``teams.owner_id`` FKs to ``auth.users``; nothing here needs a real
    signup, just a row to point at."""
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        uid,
        f"own-cost-{uuid.uuid4().hex[:8]}@test.dev",
    )
    return uid


async def _mk_conversation(conn) -> int:
    """``conversations`` wants type / scope_id / created_by, and ``scope_id``
    FKs to ``teams`` — which in turn wants name / owner_id / invite_code."""
    owner = await _mk_user(conn)
    suffix = uuid.uuid4().hex[:8]
    team = await conn.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"Own Cost Team {suffix}",
        owner,
        f"OWNCOST{suffix}",
    )
    return await conn.fetchval(
        "INSERT INTO public.conversations (type, scope_id, created_by) "
        "VALUES ('direct_agent', $1, $2) RETURNING id",
        team,
        owner,
    )


async def _insert_run(
    conn,
    rid: int,
    agent: uuid.UUID,
    *,
    cost: str | None = None,
    cost_cents: str | None = None,
    parent: int | None = None,
    root: int | None = None,
    issue: int | None = None,
    conversation: int | None = None,
) -> None:
    """One ``agent_runs`` row. ``cost`` is the jsonb ``metadata_json.cost``
    view as the writers store it; omitting it is the "no cost view" shape the
    tier-2 and tier-3 arms key on."""
    meta = "{}" if cost is None else f'{{"cost": {cost}}}'
    await conn.execute(
        "INSERT INTO public.agent_runs "
        "  (id, agent_id, user_id, status, trigger, cost_cents, "
        "   parent_run_id, root_run_id, issue_id, conversation_id, metadata_json) "
        "VALUES ($1, $2, $3, 'completed', 'manual', $4, $5, $6, $7, $8, $9::jsonb)",
        rid,
        agent,
        uuid.uuid4(),
        None if cost_cents is None else decimal.Decimal(cost_cents),
        parent,
        root,
        issue,
        conversation,
        meta,
    )


async def _own(conn, rid: int):
    return await conn.fetchval(
        "SELECT own_cost_cents FROM public.agent_runs WHERE id = $1", rid
    )


@_skip
async def test_tier1_the_cost_view_wins_over_the_old_column(conn):
    """The definition, in one row: own + media, and nothing else.

    ``by_child`` is the descendants' spend — the very thing ``cost_cents``
    already conflates in. If the sum reached into it, the new column would
    inherit the double-count it exists to remove. The stale 99 on the old
    column is likewise ignored: tier 1 recomputes, it does not copy.
    """
    agent = await _mk_agent(conn)
    await _insert_run(
        conn,
        90001,
        agent,
        cost='{"own_cents": 1.25, "media_cents": 0.5, "by_child": {"x": 9}}',
        cost_cents="99",
    )

    await conn.execute(_body())

    assert await _own(conn, 90001) == decimal.Decimal("1.75")


@_skip
async def test_tier2_no_view_and_no_children_copies_the_old_column(conn):
    """No descendants means nothing to over-count: the old column IS this
    run's own spend, so it can be carried across as-is."""
    agent = await _mk_agent(conn)
    await _insert_run(conn, 90002, agent, cost_cents="3.5")

    await conn.execute(_body())

    assert await _own(conn, 90002) == decimal.Decimal("3.5")


@_skip
async def test_tier3_no_view_but_children_stays_null(conn):
    """Where the two meanings are already mixed, the migration declines to
    guess — NULL says "never computed", and the readers COALESCE it to 0.

    Guessing here would be the worst outcome available: a plausible number
    that silently carries the child's spend into a column documented as
    excluding it. The leaf beside it proves the NULL is a decision about this
    row, not the tier-2 arm failing to fire.
    """
    agent = await _mk_agent(conn)
    await _insert_run(conn, 90003, agent, cost_cents="7")
    await _insert_run(conn, 90004, agent, cost_cents="2", parent=90003, root=90003)

    await conn.execute(_body())

    assert await _own(conn, 90003) is None
    assert await _own(conn, 90004) == decimal.Decimal("2")


@_skip
async def test_a_child_inherits_the_issue_id_from_its_root(conn):
    """Sub-runs written before 3c taught agent_worker to stamp ``issue_id``
    carry NULL. Once the readers stop filtering to root rows, those rows would
    drop straight out of the per-issue sum — the under-count this whole change
    is meant to end, reintroduced through the other door.
    """
    agent = await _mk_agent(conn)
    issue = await _mk_issue(conn, agent)
    await _insert_run(conn, 90005, agent, cost_cents="1", issue=issue)
    await _insert_run(conn, 90006, agent, cost_cents="1", parent=90005, root=90005)

    await conn.execute(_body())

    assert (
        await conn.fetchval("SELECT issue_id FROM public.agent_runs WHERE id = 90006")
        == issue
    )


@_skip
async def test_a_child_inherits_the_conversation_id_on_its_own(conn):
    """Step ④ copies two columns, and the second one needs its own case.

    ``issue_id`` and ``conversation_id`` are carried by **separate** OR arms
    and separate COALESCEs, so a suite that only ever builds rows where both
    are NULL together cannot tell the two apart — deleting the
    ``conversation_id`` assignment, or mistyping the second arm to test
    ``r.issue_id``, would sail through it.

    So this is the asymmetric row only the second arm reaches: the child
    already HAS an ``issue_id`` of its own (arm one is false for it) and the
    root has none, while the conversation binding exists only on the root.
    Getting here requires the arm to look at ``conversation_id`` on both
    sides, and the SET to actually assign it.

    The child's own ``issue_id`` is asserted too: ``COALESCE(c.issue_id,
    r.issue_id)`` must leave a value already present alone rather than
    overwriting it with the root's NULL.
    """
    agent = await _mk_agent(conn)
    conversation = await _mk_conversation(conn)
    issue = await _mk_issue(conn, agent)

    await _insert_run(conn, 90009, agent, cost_cents="1", conversation=conversation)
    await _insert_run(
        conn, 90010, agent, cost_cents="1", parent=90009, root=90009, issue=issue
    )

    await conn.execute(_body())

    child = await conn.fetchrow(
        "SELECT issue_id, conversation_id FROM public.agent_runs WHERE id = 90010"
    )
    assert child["conversation_id"] == conversation
    assert child["issue_id"] == issue


@_skip
async def test_running_it_twice_changes_nothing(conn):
    """``run-migration`` batches files into one psql session and a re-run is
    always possible, so the whole file has to survive being applied twice —
    ``ADD COLUMN IF NOT EXISTS`` included.
    """
    agent = await _mk_agent(conn)
    await _insert_run(conn, 90007, agent, cost='{"own_cents": 1}', cost_cents="50")

    await conn.execute(_body())
    await conn.execute(_body())

    assert await _own(conn, 90007) == decimal.Decimal("1")


@_skip
async def test_a_rerun_does_not_clobber_what_the_writers_already_set(conn):
    """What the ``own_cost_cents IS NULL`` guards are actually for.

    Both tiers recompute from inputs that do not move, so a second pass is a
    no-op with or without the guards — which makes "it ran twice" a weak test,
    and it is the wrong one. The case that bites arrives with PR-B: once
    ``RunEventWriter`` / ``RunRecorder`` write this column live, a row can hold
    a correct own-spend of 1 while ``cost_cents`` sits at 50 because a
    descendant reported up. Tier 2 selects exactly that shape — no cost view,
    no children — so an unguarded re-run would overwrite the right number with
    the contaminated one, silently, on a migration re-apply nobody thinks of as
    a write.

    The 1 is installed the way PR-B will install it (after the column exists),
    then the file is replayed.
    """
    agent = await _mk_agent(conn)
    await _insert_run(conn, 90008, agent, cost_cents="50")

    await conn.execute(_body())
    assert await _own(conn, 90008) == decimal.Decimal("50")  # tier 2 seeded it

    await conn.execute(
        "UPDATE public.agent_runs SET own_cost_cents = 1 WHERE id = 90008"
    )
    await conn.execute(_body())

    assert await _own(conn, 90008) == decimal.Decimal("1")
