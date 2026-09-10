"""Integration tests for the ORM-only AgentWorkforceRepository (formerly the
Phase 2 H batch, highest-risk) against real PG. The M2 Persistent Workforce:
5 tables in one repo.

THE H-BATCH CRASHER PROOF. ~17 bare ``UUID(row[...])`` consumers live across
``app/services/workforce/*``. ``UUID(native_uuid.UUID)`` raises ``TypeError``
(UUID() wants str/bytes/int). So the central guarantee these tests assert is:
EVERY uuid column this repo returns is a STR, so ``UUID(returned)`` works.

We replay the EXACT crasher pattern from the real consumers:
  - inbox: ``UUID(message["id"])``       (agent_inbox.id)
  - task:  ``UUID(task["agent_id"])`` / ``UUID(task["user_id"])`` (agent_worker)
  - outbox:``UUID(row["id"])`` / ``UUID(row["recipient_agent_id"])`` /
           ``UUID(row["sender_agent_id"])`` (outbox_dispatcher)
  - task["id"] = dbos_workflow_id (TEXT) → already str (UUID() works).

Plus: per-table value-type assertions, write round-trips (incl. a task_tracking
business-column write that does NOT touch a trigger-owned workflow column —
agent_task rows are app-owned), upsert ON CONFLICT, the v3 date-filter boundary
(list_stale_workers heartbeat cutoff), and the flag-free factory.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_agent_workforce_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TITLE_PREFIX = "__test_orm_workforce_"


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
async def agent_id(integration_db_url):
    """A real ai_agents.id. agent_workers.agent_id / agent_inbox.recipient_agent_id
    / agent_outbox.sender_agent_id / agent_state_history.agent_id all FK → it.
    task_tracking.agent_id FK is SET-NULL but we set it to this id. Reuses an
    existing agent if present; else seeds a throwaway and cleans it up."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        existing = await conn.fetchval("SELECT id FROM ai_agents LIMIT 1")
        if existing is not None:
            yield existing
            return
        seeded = await conn.fetchval(
            "INSERT INTO ai_agents (name, slug) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING RETURNING id",
            "__test_orm_workforce_agent",
            f"__test-orm-wf-{uuid.uuid4().hex[:8]}",
        )
        try:
            yield seeded
        finally:
            await conn.execute("DELETE FROM ai_agents WHERE id = $1", seeded)
    finally:
        await conn.close()


@pytest.fixture
async def user_id(integration_db_url):
    """A real auth.users id (task_tracking.user_id FK → auth.users).

    Seeds a throwaway row when the database has none, the same way ``agent_id``
    above seeds an agent. It used to ``pytest.skip`` instead — harmless against
    a populated dev database, fatal on the ephemeral schema-drift one, which is
    built from the baseline and is therefore ALWAYS empty. Six tests take this
    fixture, including all three dispatch round-trips, so skipping would have
    left the file passing on CI while the statements it exists to execute never
    ran. ``pytest-no-full-skip.sh`` cannot catch that: seven other tests pass,
    so the step is green. A fixture that skips is a fixture that can hide."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if uid is not None:
            yield uid
            return
        seeded = uuid.uuid4()
        await conn.execute("INSERT INTO auth.users (id) VALUES ($1)", seeded)
        try:
            yield seeded
        finally:
            await conn.execute("DELETE FROM auth.users WHERE id = $1", seeded)
    finally:
        await conn.close()


@pytest.fixture
async def cleanup(integration_db_url, agent_id):
    """Tear down every row this test creates for ``agent_id``. Order matters:
    task_tracking first (agent_id FK is SET NULL, so it survives an ai_agents
    delete — clean it explicitly), then workers/inbox/history/outbox."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM task_tracking WHERE task_kind = 'agent_task' "
            "AND title LIKE $1",
            _TITLE_PREFIX + "%",
        )
        await conn.execute(
            "DELETE FROM agent_state_history WHERE agent_id = $1", agent_id
        )
        await conn.execute(
            "DELETE FROM agent_inbox WHERE recipient_agent_id = $1", agent_id
        )
        await conn.execute(
            "DELETE FROM agent_outbox WHERE sender_agent_id = $1", agent_id
        )
        await conn.execute("DELETE FROM agent_workers WHERE agent_id = $1", agent_id)
    finally:
        await conn.close()


def _repo():
    from app.repositories.agent_workforce_repository import (
        AgentWorkforceRepository,
    )

    return AgentWorkforceRepository()


def _title() -> str:
    return f"{_TITLE_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── 1. Workers: upsert ON CONFLICT, state update, heartbeat, parity ─────


async def test_upsert_worker_on_conflict_and_parity(
    integration_db_url, patched_engine, agent_id, cleanup
):
    repo = _repo()
    row = await repo.upsert_worker(agent_id=UUID(str(agent_id)), state="idle")
    assert row is not None
    # agent_id (uuid PK) → str (so UUID(row["agent_id"]) works).
    assert type(row["agent_id"]) is str
    assert UUID(row["agent_id"]) == UUID(str(agent_id))
    # timestamptz → ISO str.
    assert type(row["heartbeat_at"]) is str and "T" in row["heartbeat_at"]
    # worker_pid stays native int / None (not str()'d).
    assert row["worker_pid"] is None

    # ON CONFLICT: a second upsert updates the same PK row (idempotent).
    again = await repo.upsert_worker(
        agent_id=UUID(str(agent_id)), state="working", worker_pid=4242
    )
    assert again["state"] == "working"
    assert again["worker_pid"] == 4242
    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM agent_workers WHERE agent_id = $1", agent_id
        )
    finally:
        await conn.close()
    assert cnt == 1  # upsert, not duplicate insert

    # get_worker round-trips the same str shape.
    got = await repo.get_worker(UUID(str(agent_id)))
    assert got is not None and type(got["agent_id"]) is str


async def test_update_worker_state_and_heartbeat(patched_engine, agent_id, cleanup):
    repo = _repo()
    await repo.upsert_worker(agent_id=UUID(str(agent_id)), state="idle")
    ok = await repo.update_worker_state(agent_id=UUID(str(agent_id)), state="working")
    assert ok is True
    assert await repo.heartbeat(UUID(str(agent_id))) is True
    # Non-existent agent → False (soft-fail contract, no raise).
    assert await repo.heartbeat(uuid.uuid4()) is False


async def test_list_stale_workers_date_filter_boundary(
    patched_engine, agent_id, cleanup
):
    """v3 temporal-filter rule: stale_before is a NATIVE aware datetime bound
    directly in ``heartbeat_at < cutoff``. Boundary: a fresh worker is NOT
    stale; a cutoff in the future DOES include it."""
    repo = _repo()
    await repo.upsert_worker(agent_id=UUID(str(agent_id)), state="idle")
    now = datetime.now(timezone.utc)

    fresh = await repo.list_stale_workers(stale_before=now - timedelta(hours=1))
    assert all(w["agent_id"] != str(agent_id) for w in fresh)

    future = await repo.list_stale_workers(stale_before=now + timedelta(hours=1))
    assert any(w["agent_id"] == str(agent_id) for w in future)


# ─── 2. Inbox: ★ THE CRASHER — message id must be str for UUID() ─────────


async def test_inbox_id_is_str_so_UUID_consumer_works(
    patched_engine, agent_id, cleanup
):
    """Replay inbox_processor's ``UUID(message["id"])``. A native uuid.UUID
    there CRASHES (TypeError). Prove every inbox read returns id as str."""
    repo = _repo()
    msg = await repo.enqueue_inbox(
        recipient_agent_id=UUID(str(agent_id)),
        sender_kind="user",
        message_type="task",
        payload={"text": "hi"},
        sender_user_id=uuid.uuid4(),
    )
    assert msg is not None
    assert type(msg["id"]) is str
    # The exact consumer pattern — must NOT raise.
    assert isinstance(UUID(msg["id"]), UUID)
    # sender_user_id (uuid) also str; payload jsonb → native dict.
    assert type(msg["sender_user_id"]) is str
    assert msg["payload"] == {"text": "hi"}

    # claim_next_unread flips unread→reading and returns the str-id row.
    claimed = await repo.claim_next_unread(
        recipient_agent_id=UUID(str(agent_id)), claimed_by="test"
    )
    assert claimed is not None
    assert claimed["status"] == "reading"
    assert isinstance(UUID(claimed["id"]), UUID)

    # mark processed (business write) succeeds.
    assert await repo.mark_inbox_processed(message_id=UUID(msg["id"])) is True

    listed = await repo.list_inbox(recipient_agent_id=UUID(str(agent_id)))
    assert listed["total"] >= 1
    assert all(isinstance(UUID(i["id"]), UUID) for i in listed["items"])


# ─── 3. Tasks (task_tracking agent_task rows): crasher + business write ──


async def test_task_uuid_columns_are_str_for_UUID_consumer(
    integration_db_url, patched_engine, agent_id, user_id, cleanup
):
    """Replay agent_worker's ``UUID(task["agent_id"])`` / ``UUID(task["user_id"])``
    and ``UUID(task["id"])``. agent_id/user_id are uuid cols → MUST be str; id
    (=dbos_workflow_id) is TEXT → already str. All three UUID() calls must work."""
    repo = _repo()
    title = _title()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={"goal": "summarize"},
        title=title,
    )
    assert created is not None
    # The exact crasher consumers — none may raise.
    assert isinstance(UUID(created["id"]), UUID)  # dbos_workflow_id (TEXT)
    assert isinstance(UUID(created["agent_id"]), UUID)  # uuid → str
    assert isinstance(UUID(created["user_id"]), UUID)  # uuid → str
    assert type(created["agent_id"]) is str
    assert type(created["user_id"]) is str
    # Shape-mapper fields: lifecycle_status from phase, payload from metadata.
    assert created["lifecycle_status"] == "queued"
    assert created["payload"] == {"goal": "summarize"}

    # committed?
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT count(*) FROM task_tracking WHERE dbos_workflow_id = $1",
            created["id"],
        )
    finally:
        await conn.close()
    assert persisted == 1

    # get / list round-trip the str shape.
    got = await repo.get_task(UUID(created["id"]))
    assert got is not None and isinstance(UUID(got["agent_id"]), UUID)
    listed = await repo.list_tasks(agent_id=UUID(str(agent_id)))
    assert listed["total"] >= 1


async def test_task_business_write_no_trigger_column_clobber(
    integration_db_url, patched_engine, agent_id, user_id, cleanup
):
    """task_tracking discipline: this repo writes ONLY agent_task rows (the
    trigger doesn't mirror them — app code owns their phase/status). Prove the
    lifecycle write lands AND task_kind stays 'agent_task' (we never convert it
    into a workflow row)."""
    repo = _repo()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={},
        title=_title(),
    )
    task_id = UUID(created["id"])

    # claim → assigned (CAS), then in_progress, then done.
    claimed = await repo.claim_task(created["id"], workflow_id="wf-int-1")
    assert claimed is not None and claimed["lifecycle_status"] == "assigned"

    assert await repo.update_task_status(
        task_id=task_id, lifecycle_status="in_progress", current_run_id="999"
    )
    assert await repo.update_task_status(
        task_id=task_id, lifecycle_status="done", result={"ok": True}
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT task_kind, phase, status, started_at, completed_at, metadata "
            "FROM task_tracking WHERE dbos_workflow_id = $1",
            created["id"],
        )
    finally:
        await conn.close()
    assert row["task_kind"] == "agent_task"  # still an app-owned row
    assert row["phase"] == "done"  # 8-state precision preserved
    assert row["status"] == "completed"  # 5-state mapping
    assert row["started_at"] is not None  # in_progress set it
    assert row["completed_at"] is not None  # done set it
    # metadata read-modify-write preserved agent_payload + spliced run/result.
    # (asyncpg returns the raw jsonb as a str; the ORM path returns native dict —
    # decode here just to assert the persisted content.)
    import json as _json

    md = row["metadata"]
    if isinstance(md, str):
        md = _json.loads(md)
    assert md.get("current_run_id") == "999"
    assert md.get("agent_result") == {"ok": True}


async def test_requeue_task(patched_engine, agent_id, user_id, cleanup):
    repo = _repo()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={},
        title=_title(),
    )
    task_id = UUID(created["id"])
    await repo.claim_task(created["id"], workflow_id="wf-int-requeue")
    assert await repo.requeue_task(task_id) is True
    got = await repo.get_task(task_id)
    assert got["lifecycle_status"] == "queued"


# ─── 4. State history ────────────────────────────────────────────────────


async def test_log_and_list_state_history(patched_engine, agent_id, cleanup):
    repo = _repo()
    ok = await repo.log_state_transition(
        agent_id=UUID(str(agent_id)),
        from_state="idle",
        to_state="working",
        trigger="claim",
        metadata={"note": "x"},
    )
    assert ok is True
    rows = await repo.list_state_history(agent_id=UUID(str(agent_id)))
    assert len(rows) >= 1
    # id / agent_id (uuid) → str.
    assert type(rows[0]["id"]) is str
    assert isinstance(UUID(rows[0]["agent_id"]), UUID)
    assert rows[0]["metadata_json"] == {"note": "x"}


# ─── 5. Outbox: ★ THE CRASHER — id/recipient/sender must be str ─────────


async def test_outbox_uuid_columns_are_str_for_UUID_consumer(
    patched_engine, agent_id, cleanup
):
    """Replay outbox_dispatcher's ``UUID(row["id"])`` /
    ``UUID(row["recipient_agent_id"])`` / ``UUID(row["sender_agent_id"])``."""
    repo = _repo()
    row = await repo.enqueue_outbox(
        sender_agent_id=UUID(str(agent_id)),
        recipient_kind="user",
        message_type="notification",
        payload={"msg": "done"},
        recipient_user_id=uuid.uuid4(),
    )
    assert row is not None
    assert isinstance(UUID(row["id"]), UUID)  # agent_outbox.id
    assert isinstance(UUID(row["sender_agent_id"]), UUID)
    assert type(row["recipient_user_id"]) is str
    assert row["delivered"] is False
    assert row["payload"] == {"msg": "done"}

    listed = await repo.list_undelivered_outbox()
    assert any(r["id"] == row["id"] for r in listed)
    # The f-string consumer (dedup_key=f"outbox:{row['id']}") works on str.
    assert f"outbox:{row['id']}".startswith("outbox:")

    assert await repo.mark_outbox_delivered(UUID(row["id"])) is True


# ─── factory: ORM-only (flag retired, class collapsed) ──────────────────────


async def test_factory_returns_orm_repository():
    """Post-collapse the factory unconditionally returns the (ORM-only)
    AgentWorkforceRepository — no flag branch, no ORM subclass."""
    from app.repositories.agent_workforce_repository import (
        AgentWorkforceRepository,
        get_agent_workforce_repository,
    )

    assert type(get_agent_workforce_repository()) is AgentWorkforceRepository


# ─── 6. Dispatch bookkeeping (2b-2 T3) ───────────────────────────────────
#
# These four statements are the ones the unit suite can only compile, never
# execute: an UPDATE ... RETURNING an ORM entity, a JSONB ``->>`` IS NULL
# filter, a ``coalesce(col,'{}') || patch`` merge, and a CASE-per-arm CAS.
# A stubbed session accepts all of them regardless of what Postgres thinks.


async def test_claim_task_cas_arms_against_real_pg(
    patched_engine, agent_id, user_id, cleanup
):
    """Both CAS arms, executed: a fresh claim, a same-workflow re-entry, and a
    foreign workflow that must be refused."""
    repo = _repo()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={"prompt": "hi"},
        title=_title(),
    )
    tid = created["id"]
    mine = "workforce-int-1"

    first = await repo.claim_task(tid, workflow_id=mine)
    assert first is not None and first["lifecycle_status"] == "assigned"
    assert first["workforce_workflow_id"] == mine

    # A DIFFERENT workflow cannot steal a row that is no longer queued.
    assert await repo.claim_task(tid, workflow_id="workforce-int-2") is None

    # Our own replay walks back in, and does NOT knock the phase backwards.
    assert await repo.update_task_status(
        task_id=UUID(tid), lifecycle_status="in_progress"
    )
    again = await repo.claim_task(tid, workflow_id=mine)
    assert again is not None
    assert again["lifecycle_status"] == "in_progress"


async def test_dispatch_list_and_stamp_round_trip(
    patched_engine, agent_id, user_id, cleanup
):
    """``list_undispatched_queued_tasks`` → ``mark_dispatched`` → gone from the
    list; ``requeue_task`` brings it back with the attempt counter intact."""
    repo = _repo()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={},
        title=_title(),
    )
    tid = created["id"]

    ids = {t["id"] for t in await repo.list_undispatched_queued_tasks(limit=500)}
    assert tid in ids

    await repo.mark_dispatched(tid, workflow_id=f"workforce-{tid}-1", attempt=1)
    ids = {t["id"] for t in await repo.list_undispatched_queued_tasks(limit=500)}
    assert tid not in ids  # the ->> IS NULL filter really excludes it

    got = await repo.get_task(UUID(tid))
    assert got["dispatch_attempt"] == 1
    assert got["workforce_workflow_id"] == f"workforce-{tid}-1"

    # Requeue clears the stamp and the ownership token but KEEPS the counter,
    # so the next dispatch derives an id DBOS has never seen.
    await repo.claim_task(tid, workflow_id=f"workforce-{tid}-1")
    assert await repo.requeue_task(UUID(tid)) is True
    back = await repo.get_task(UUID(tid))
    assert back["lifecycle_status"] == "queued"
    assert back["dispatch_attempt"] == 1
    assert back["workforce_workflow_id"] is None
    ids = {t["id"] for t in await repo.list_undispatched_queued_tasks(limit=500)}
    assert tid in ids


async def test_count_inflight_counts_assigned_too(
    patched_engine, agent_id, user_id, cleanup
):
    """'assigned' is where a task whose worker died comes to rest, so the gauge
    has to see it — that is the one state an operator most needs surfaced."""
    repo = _repo()
    created = await repo.create_task(
        agent_id=UUID(str(agent_id)),
        user_id=UUID(str(user_id)),
        payload={},
        title=_title(),
    )
    before = await repo.count_inflight_agent_tasks()
    assert before >= 1  # queued counts

    await repo.claim_task(created["id"], workflow_id="workforce-count-1")
    assert await repo.count_inflight_agent_tasks() >= before  # assigned still counts

    assert await repo.update_task_status(
        task_id=UUID(created["id"]), lifecycle_status="done"
    )
    assert await repo.count_inflight_agent_tasks() == before - 1
