"""R1 regression: a missing task_tracking row must NOT crash the workflow.
Before the fix, _get_phase used .single() -> PGRST116 on 0 rows, killing
soda_download_workflow on its first manager.start() call."""
from __future__ import annotations

import pytest

from app.services.infra.unified_task_manager import TaskPhase, UnifiedTaskManager


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal async PostgREST stub: maybe_single().execute() -> data=None."""
    def table(self, *_a, **_k): return self
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def maybe_single(self): return self
    def single(self):  # if code still calls .single(), make the test loud
        raise AssertionError("_get_phase must use maybe_single(), not single()")
    async def execute(self): return _Resp(None)  # 0 rows
    def insert(self, *_a, **_k): return self
    def update(self, *_a, **_k): return self


@pytest.fixture
def mgr(monkeypatch):
    m = UnifiedTaskManager()
    async def _client():
        return _Query()
    monkeypatch.setattr(m, "_get_client", _client)
    return m


async def test_get_phase_missing_row_returns_queued(mgr):
    phase = await mgr._get_phase("wf-does-not-exist")
    assert phase == TaskPhase.QUEUED


async def test_start_missing_row_does_not_raise(mgr):
    await mgr.start("wf-does-not-exist")
