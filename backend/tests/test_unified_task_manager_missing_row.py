"""R1 regression: a missing task_tracking row must NOT crash the workflow.
Before the fix, _get_phase used .single() -> PGRST116 on 0 rows, killing
soda_download_workflow on its first manager.start() call.

Ported to the ORM session boundary: `_get_phase` / `_row_exists` /
`_atomic_update` now open `read_scope()` / `write_scope()` (imported
locally inside each method from `app.db.session`), so the fake here stands
in for the SQLAlchemy `AsyncSession` rather than a PostgREST client.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.infra.unified_task_manager import TaskPhase, UnifiedTaskManager


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v


class _Session:
    """Returns one queued result per execute() call, in order. A row that
    doesn't exist is represented by ``None`` — matching a 0-row
    ``.scalar()``/``.mappings().first()`` result."""

    def __init__(self, results):
        self._results = list(results)
        self.call_count = 0

    async def execute(self, stmt, params=None):
        self.call_count += 1
        return _Result(self._results.pop(0) if self._results else None)


@pytest.fixture
def patch_scopes(monkeypatch):
    def _install(results) -> _Session:
        session = _Session(results)

        @asynccontextmanager
        async def _scope():
            yield session

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)
        monkeypatch.setattr(dbs, "write_scope", _scope)
        return session

    return _install


async def test_get_phase_missing_row_returns_queued(patch_scopes):
    patch_scopes([None])  # _get_phase's select().scalar() → 0 rows
    mgr = UnifiedTaskManager()
    phase = await mgr._get_phase("wf-does-not-exist")
    assert phase == TaskPhase.QUEUED


async def test_start_missing_row_does_not_raise(patch_scopes):
    # Call order inside start(): _row_exists (None -> missing) ->
    # self.create() self-heal (INSERT .scalar() -> None, non-fatal) ->
    # _get_phase (None -> QUEUED) -> _atomic_update (UPDATE, result unused).
    patch_scopes([None, None, None, None])
    mgr = UnifiedTaskManager()
    await mgr.start("wf-does-not-exist")


async def test_start_missing_row_selfheals_create(monkeypatch, patch_scopes):
    """When start() finds no row, it creates a minimal one so downstream
    update_progress/complete have a row to update."""
    created = {}

    # create() is mocked below, so only _row_exists, _get_phase, and
    # _atomic_update touch the session (3 execute calls).
    patch_scopes([None, None, None])
    m = UnifiedTaskManager()

    async def _fake_create(**kwargs):
        created.update(kwargs)
        return kwargs.get("dbos_workflow_id")

    monkeypatch.setattr(m, "create", _fake_create)

    await m.start("wf-missing", user_id="u-123")
    assert created.get("dbos_workflow_id") == "wf-missing"
    assert created.get("user_id") == "u-123"


async def test_start_selfheal_create_failure_is_swallowed(monkeypatch, patch_scopes):
    """Self-heal must never crash the workflow: if create() raises (e.g. the
    realistic UUID-NOT-NULL INSERT failure), start() swallows and continues."""
    patch_scopes([None, None, None])
    m = UnifiedTaskManager()

    async def _boom(**kwargs):
        raise Exception("boom")

    monkeypatch.setattr(m, "create", _boom)

    # Must not raise.
    await m.start("wf-missing", user_id="u-1")
