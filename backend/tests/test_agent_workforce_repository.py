"""Unit tests for AgentWorkforceRepository (M2) — ORM 2.0, mocked session.

Post-rollout the repo is the SQLAlchemy 2.0 implementation: reads go through
``read_scope()`` (``select``) and writes through ``write_scope()``
(``pg_insert ... on_conflict_do_update`` / ``update ... returning``). These
tests mock both scopes with a fake session that records the compiled SQL + bound
params of every statement and returns scripted ORM rows (real transient model
instances, so ``_orm_obj_to_dict`` yields a parity dict) — covering the contract
corners the state machine and dispatcher depend on WITHOUT a live DB:

    - claim_next_unread  → CAS guard binds status='unread'
    - claim_task         → single CAS UPDATE ... RETURNING on phase='queued'
    - enqueue_inbox      → dedup collision falls back to lookup
    - create_task        → self-references root_task_id when tree root
    - update_task_status → terminal status binds completed_at
    - requeue_task       → only acts on assigned/in_progress (phase IN)

The DSN-gated integration suite in
``tests/integration/test_agent_workforce_repository_orm.py`` exercises the real
round-trip + the crasher-proof uuid→str parity, including the four dispatch
statements a stubbed session can only compile (claim_task's two CAS arms,
the ``->>`` IS NULL dispatch list, the ``||`` metadata merges, the inflight
count). That file runs on every schema-drift.yml build (its own step, through
pytest-no-full-skip.sh), so those statements are executed against a real
Postgres on each PR that touches this repository. Green HERE still is not
green on Postgres — a stubbed session accepts SQL the server would reject —
which is exactly why that file exists; do not add a statement here without
giving it a case there.

A4 (migration 200) note: tasks live in task_tracking[task_kind='agent_task'];
8-state lifecycle precision is preserved in the `phase` column while the
trigger/applayer-shared `status` column carries the 5-state mirror.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import pytest

import app.repositories.agent_workforce_repository as mod
from app.models import AgentInbox, AgentWorkers, TaskTracking
from app.repositories.agent_workforce_repository import AgentWorkforceRepository

# ─── fake session infra ───────────────────────────────────────────────


class _Scalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _Result:
    """A stand-in for a SQLAlchemy Result. ``scalars`` feeds
    ``.scalars().first()/.all()``; ``first`` feeds ``.first()`` (the tuple-row
    selects in update_task_status / requeue_task)."""

    def __init__(
        self, *, scalars: Optional[list[Any]] = None, first: Any = None
    ) -> None:
        self._scalars = scalars
        self._first = first

    def scalars(self) -> _Scalars:
        return _Scalars(self._scalars or [])

    def first(self) -> Any:
        return self._first


class _FakeSession:
    """Returns a scripted Result per execute() call (sequenced) and records the
    compiled SQL text + bound params of every statement. A scripted Exception is
    raised (to drive the dedup-collision swallow)."""

    def __init__(
        self, responses: list[Any], scalar_values: Optional[list[Any]] = None
    ) -> None:
        self._responses = list(responses)
        self._scalars = list(scalar_values or [])
        self.sql: list[str] = []
        self.params: list[dict] = []

    def _record(self, stmt: Any) -> None:
        compiled = stmt.compile()
        self.sql.append(str(compiled))
        self.params.append(dict(compiled.params))

    async def execute(self, stmt: Any) -> _Result:
        self._record(stmt)
        resp = self._responses.pop(0) if self._responses else _Result()
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def scalar(self, stmt: Any) -> Any:
        self._record(stmt)
        return self._scalars.pop(0) if self._scalars else 0


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(monkeypatch, session: _FakeSession) -> None:
    """Route both read_scope and write_scope to the same fake session."""
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))


@pytest.fixture
def repo():
    return AgentWorkforceRepository()


# ─── workers ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_worker_payload_idle_default(repo, monkeypatch):
    agent_id = uuid4()
    row = AgentWorkers(agent_id=str(agent_id), state="idle", worker_pid=1234)
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    out = await repo.upsert_worker(agent_id=agent_id, worker_pid=1234)

    assert out is not None
    assert "ON CONFLICT" in session.sql[0]
    p = session.params[0]
    assert p["agent_id"] == str(agent_id)
    assert p["state"] == "idle"
    assert p["worker_pid"] == 1234


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_stale_workers_filters_active_states(repo, monkeypatch):
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
    await repo.list_stale_workers(stale_before=stale_before)

    p = session.params[0]
    # in_() renders as an expanding bindparam carrying the full list.
    assert set(p["state_1"]) == {"idle", "working", "waiting_for_other"}
    # v3 temporal filter: the native aware datetime is bound directly.
    assert p["heartbeat_at_1"] == stale_before


# ─── inbox ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_unread_uses_cas_guard(repo, monkeypatch):
    """Update must filter on status='unread' to prevent double-claim."""
    message_id = str(uuid4())
    updated = AgentInbox(id=message_id, status="reading")
    # 1st execute (select id) → scalar message_id; 2nd (update) → updated row.
    session = _FakeSession([_Result(scalars=[message_id]), _Result(scalars=[updated])])
    _patch_scopes(monkeypatch, session)

    out = await repo.claim_next_unread(
        recipient_agent_id=uuid4(), claimed_by="hostA:42"
    )
    assert out is not None
    # The CAS guard: status='unread' bound in the UPDATE's WHERE; SET status='reading'.
    upd_params = session.params[1]
    assert upd_params["status_1"] == "unread"
    assert upd_params["status"] == "reading"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_unread_returns_none_on_empty_inbox(repo, monkeypatch):
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)
    out = await repo.claim_next_unread(recipient_agent_id=uuid4(), claimed_by="hostA:1")
    assert out is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_inbox_dedup_collision_falls_back_to_lookup(repo, monkeypatch):
    """Unique-violation on dedup_key → return the existing row via lookup."""
    existing = AgentInbox(id=str(uuid4()), status="unread")
    # 1st execute (insert) raises 'duplicate'; 2nd (dedup lookup) → existing row.
    session = _FakeSession(
        [Exception("duplicate key value"), _Result(scalars=[existing])]
    )
    _patch_scopes(monkeypatch, session)

    out = await repo.enqueue_inbox(
        recipient_agent_id=uuid4(),
        sender_kind="user",
        message_type="task",
        payload={"x": 1},
        dedup_key="user-msg-001",
    )
    assert out is not None
    assert out["id"] == str(existing.id)
    assert out["status"] == "unread"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_inbox_processed_attaches_task_id(repo, monkeypatch):
    session = _FakeSession([_Result(scalars=[str(uuid4())])])
    _patch_scopes(monkeypatch, session)

    task_id = uuid4()
    await repo.mark_inbox_processed(message_id=uuid4(), task_id=task_id)

    p = session.params[0]
    assert p["status"] == "processed"
    assert p["task_id"] == str(task_id)


# ─── tasks ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_writes_the_issue_id_column(repo, monkeypatch):
    """Task 7a defect 3: the background sub-agent's row carried
    ``metadata.agent_payload.issue_id`` while the ``issue_id`` COLUMN stayed
    NULL, so any lookup of "this issue's agent tasks" came back empty. It is a
    business decoration (route C rule 3), so the app layer writes it."""
    row = TaskTracking(dbos_workflow_id="echo", phase="queued", metadata_={})
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    await repo.create_task(
        agent_id=uuid4(),
        user_id=uuid4(),
        payload={"kind": "subagent", "issue_id": 348020765598796},
    )
    assert session.params[0]["issue_id"] == 348020765598796


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_leaves_issue_id_null_when_the_payload_has_none(
    repo, monkeypatch
):
    """System tasks (cleanup, backfills) belong to no issue — and an
    unusable value is the same as no value, never a failed insert."""
    for payload in ({"goal": "x"}, {"issue_id": None}, {"issue_id": "not-an-id"}):
        row = TaskTracking(dbos_workflow_id="echo", phase="queued", metadata_={})
        session = _FakeSession([_Result(scalars=[row])])
        _patch_scopes(monkeypatch, session)
        out = await repo.create_task(
            agent_id=uuid4(), user_id=uuid4(), payload=payload
        )
        assert out is not None, f"an unusable issue_id sank the insert: {payload}"
        assert session.params[0]["issue_id"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_accepts_a_stringified_issue_id(repo, monkeypatch):
    """Snowflake ids cross JSON as strings often enough that refusing one
    would silently drop the link on exactly the rows that need it."""
    row = TaskTracking(dbos_workflow_id="echo", phase="queued", metadata_={})
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    await repo.create_task(
        agent_id=uuid4(), user_id=uuid4(), payload={"issue_id": "348020765598796"}
    )
    assert session.params[0]["issue_id"] == 348020765598796


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_self_references_root_task_id(repo, monkeypatch):
    """A4: when parent_task_id/root_task_id are not provided, root_task_id is
    computed before INSERT (= the new task's dbos_workflow_id). Single INSERT."""
    row = TaskTracking(dbos_workflow_id="echo", phase="queued", metadata_={})
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    out = await repo.create_task(
        agent_id=uuid4(), user_id=uuid4(), payload={"prompt": "hi"}
    )
    assert out is not None
    p = session.params[0]
    new_id = p["dbos_workflow_id"]
    assert new_id, "create_task must self-generate dbos_workflow_id"
    assert p["task_kind"] == "agent_task"
    assert p["root_task_id"] == new_id, "tree root self-references"
    assert p["parent_task_id"] is None
    assert p["status"] == "pending" and p["phase"] == "queued"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_preserves_explicit_root(repo, monkeypatch):
    row = TaskTracking(dbos_workflow_id="echo", phase="queued", metadata_={})
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    explicit_parent = uuid4()
    explicit_root = uuid4()
    out = await repo.create_task(
        agent_id=uuid4(),
        user_id=uuid4(),
        payload={},
        parent_task_id=explicit_parent,
        root_task_id=explicit_root,
    )
    assert out is not None
    p = session.params[0]
    assert p["parent_task_id"] == str(explicit_parent)
    assert p["root_task_id"] == str(explicit_root)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_task_is_a_single_cas_update_with_returning(repo, monkeypatch):
    """2b-2 T3: claim-by-id replaces the by-agent ``claim_next_queued``.

    The dispatcher already picked WHICH task; the only question left is whether
    this worker owns it. One UPDATE ... RETURNING does that atomically — the
    read-then-check it replaces (``run_one_task``'s ``get_task`` + phase test)
    was not atomic, so two workers could both pass it."""
    task_id = str(uuid4())
    wf = f"workforce-{task_id}-1"
    updated = TaskTracking(dbos_workflow_id=task_id, phase="assigned", metadata_={})
    session = _FakeSession([_Result(scalars=[updated])])
    _patch_scopes(monkeypatch, session)

    out = await repo.claim_task(task_id, workflow_id=wf)

    assert out is not None and out["lifecycle_status"] == "assigned"
    # ONE statement — no separate SELECT to race against.
    assert len(session.sql) == 1
    sql = session.sql[0]
    assert "UPDATE public.task_tracking" in sql
    assert "RETURNING" in sql
    p = session.params[0]
    rendered = str(p)
    # The fresh-claim arm of the CAS.
    assert "queued" in rendered
    assert p["dbos_workflow_id_1"] == task_id
    assert p["task_kind_1"] == mod.TASK_KIND_AGENT
    # The claim always records which DBOS workflow owns the row.
    assert wf in str(p)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_task_readmits_the_same_workflow_after_a_replay(repo, monkeypatch):
    """A DBOS crash replay re-enters its OWN row.

    Without this arm the recovery path is a dead end: the worker dies between
    claim and completion, the row sits at 'assigned', the replay's CAS sees a
    phase that is no longer 'queued', returns None, and the workflow records
    SUCCESS/skipped. Nothing requeues it — ``force_terminate`` has no caller in
    ``app/`` — so the task is stranded on every surface at once. Ownership is
    keyed on the workflow id, so a DIFFERENT worker still cannot steal it."""
    task_id = str(uuid4())
    wf = f"workforce-{task_id}-1"
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)

    await repo.claim_task(task_id, workflow_id=wf)

    sql = session.sql[0]
    rendered = str(session.params[0])
    # Re-entry arm: assigned / in_progress AND the metadata id matches ours.
    assert "assigned" in rendered and "in_progress" in rendered
    assert "workforce_workflow_id" in str(session.params[0])
    assert wf in str(session.params[0])
    # Phase only advances out of 'queued' — a re-entered in_progress row keeps
    # its phase rather than being knocked back to 'assigned'.
    assert "CASE" in sql.upper()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_task_returns_none_when_someone_else_won(repo, monkeypatch):
    """Zero rows updated == another worker holds it. Not an error."""
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)
    assert await repo.claim_task(str(uuid4()), workflow_id="workforce-x-1") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_undispatched_queued_tasks_filters_on_missing_stamp(
    repo, monkeypatch
):
    """The dispatch tick's work list: queued agent_tasks with no
    ``metadata.dispatched_at``. The ``->>`` IS NULL is the whole point — a row
    already enqueued must not be enqueued again on the next tick."""
    row = TaskTracking(dbos_workflow_id=str(uuid4()), phase="queued", metadata_={})
    session = _FakeSession([_Result(scalars=[row])])
    _patch_scopes(monkeypatch, session)

    out = await repo.list_undispatched_queued_tasks(limit=7)

    assert len(out) == 1
    sql = session.sql[0]
    assert "->>" in sql
    assert "IS NULL" in sql
    p = session.params[0]
    assert p["phase_1"] == "queued"
    assert p["task_kind_1"] == mod.TASK_KIND_AGENT
    assert p["param_1"] == "dispatched_at"  # the ->> key
    assert p["param_2"] == 7  # the LIMIT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_dispatched_records_the_attempt_and_its_workflow_id(
    repo, monkeypatch
):
    """JSONB ``||`` merge, not a read-modify-write: unrelated metadata keys
    (agent_payload, current_run_id) survive, and no interleaving write is lost
    between a SELECT and an UPDATE that never happen.

    It records the attempt number and the exact workflow id that was enqueued,
    because both are load-bearing: the next dispatch derives a FRESH id from
    the attempt, and ``claim_task`` re-admits a replay only when the id matches."""
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)
    task_id = str(uuid4())
    wf = f"workforce-{task_id}-3"

    await repo.mark_dispatched(task_id, workflow_id=wf, attempt=3)

    assert len(session.sql) == 1
    sql = session.sql[0]
    assert "UPDATE public.task_tracking" in sql
    assert "||" in sql
    written = str(session.params[0])
    assert "dispatched_at" in written
    assert '"dispatch_attempt": 3' in written
    assert wf in written
    # Lifecycle columns are NOT touched — this is a metadata-only stamp.
    assert "phase" not in session.params[0]
    assert "status" not in session.params[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_count_inflight_agent_tasks_is_derived_from_live_phases(
    repo, monkeypatch
):
    session = _FakeSession([], scalar_values=[4])
    _patch_scopes(monkeypatch, session)

    assert await repo.count_inflight_agent_tasks() == 4

    p = session.params[0]
    # 'assigned' belongs in the gauge: it is where a task that lost its worker
    # comes to rest, which is exactly the state an operator needs to see.
    assert set(p["phase_1"]) == {"queued", "assigned", "in_progress"}
    assert p["task_kind_1"] == mod.TASK_KIND_AGENT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_count_inflight_propagates_failure_instead_of_returning_zero(
    repo, monkeypatch
):
    """Deliberate departure from this file's soft-fail convention.

    Returning 0 for "I could not read the table" is indistinguishable from
    "the queue is empty" — the caller is a health probe, and handing it a
    reassuring zero on a dead connection is the empty-output-is-not-a-negative-
    result trap in miniature. It raises; the probe reports the gauge missing."""
    session = _FakeSession([])

    async def _boom(stmt):
        raise RuntimeError("connection reset")

    session.scalar = _boom
    _patch_scopes(monkeypatch, session)

    with pytest.raises(RuntimeError):
        await repo.count_inflight_agent_tasks()


@pytest.mark.unit
def test_claim_next_queued_is_gone():
    """The by-agent claim had zero production callers and is deleted, not
    deprecated — leaving it invites a second, non-atomic claim path."""
    assert not hasattr(AgentWorkforceRepository, "claim_next_queued")


def _update_status_session() -> _FakeSession:
    """update_task_status does SELECT metadata_ (.first()) then UPDATE returning.
    Script: metadata tuple, then the updated key."""
    return _FakeSession([_Result(first=({},)), _Result(scalars=["t"])])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_terminal_sets_completed_at(repo, monkeypatch):
    """A4: terminal status sets completed_at; task_tracking carries `status`
    (5-state mirror) and `phase` (8-state precision)."""
    for status in ("done", "failed", "cancelled"):
        session = _update_status_session()
        _patch_scopes(monkeypatch, session)
        await repo.update_task_status(task_id=uuid4(), lifecycle_status=status)

        p = session.params[1]  # the UPDATE
        assert p["phase"] == status
        expected_status = {
            "done": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[status]
        assert p["status"] == expected_status
        assert "completed_at" in p


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_in_progress_sets_started_at(repo, monkeypatch):
    session = _update_status_session()
    _patch_scopes(monkeypatch, session)
    await repo.update_task_status(task_id=uuid4(), lifecycle_status="in_progress")

    p = session.params[1]
    assert p["phase"] == "in_progress"
    assert p["status"] == "processing"
    assert "started_at" in p
    assert "completed_at" not in p


@pytest.mark.unit
@pytest.mark.asyncio
async def test_requeue_task_only_acts_on_in_flight_states(repo, monkeypatch):
    """A4: the in-flight CAS guard is on the phase column (IN assigned/in_progress)."""
    # SELECT metadata_ (.first()) then UPDATE returning.
    session = _FakeSession([_Result(first=({},)), _Result(scalars=["t"])])
    _patch_scopes(monkeypatch, session)

    await repo.requeue_task(uuid4())

    p = session.params[1]  # the UPDATE
    assert set(p["phase_1"]) == {"assigned", "in_progress"}
    assert p["phase"] == "queued"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_requeue_task_clears_the_dispatch_stamp(repo, monkeypatch):
    """A reaped task goes back to 'queued' — and must become visible to the
    dispatch tick again. The tick skips rows carrying ``dispatched_at``, so a
    requeue that leaves the stamp in place produces a task that is queued
    forever and enqueued never: a silent stall with no error anywhere."""
    md = {
        "agent_payload": {"prompt": "x"},
        "assigned_at": "2026-09-10T00:00:00+00:00",
        "dispatched_at": "2026-09-10T00:00:00+00:00",
        "dispatch_attempt": 2,
        "workforce_workflow_id": "workforce-abc-2",
        "current_run_id": "7",
    }
    session = _FakeSession([_Result(first=(md,)), _Result(scalars=["t"])])
    _patch_scopes(monkeypatch, session)

    await repo.requeue_task(uuid4())

    written = session.params[1]["metadata"]
    assert "dispatched_at" not in written
    assert "assigned_at" not in written
    assert "current_run_id" not in written
    # The dead run's ownership marker goes too — otherwise the next attempt's
    # claim could be re-admitted as a replay of a workflow that is long gone.
    assert "workforce_workflow_id" not in written
    # ⚠️ The ATTEMPT COUNTER survives. It is what makes the next dispatch use a
    # workflow id DBOS has never seen. Reset it and the re-enqueue collides
    # with the old SUCCESS row, and DBOS answers by not running it at all.
    assert written["dispatch_attempt"] == 2
    # Business payload survives — requeue is not a reset.
    assert written["agent_payload"] == {"prompt": "x"}


# ─── outbox ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_undelivered_outbox_filters_delivered_false(repo, monkeypatch):
    session = _FakeSession([_Result(scalars=[])])
    _patch_scopes(monkeypatch, session)
    await repo.list_undelivered_outbox(limit=10)
    # delivered IS false rendered as a SQL literal (not a bind).
    assert "delivered IS false" in session.sql[0]
