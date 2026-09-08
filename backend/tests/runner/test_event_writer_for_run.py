"""Phase 2a Task 3: ``RunEventWriter.for_run`` appends to a run that already
ended — continuing its seq and folding onto its stored views instead of
wiping ``metadata_json.view`` with an empty projection."""

import contextlib

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = pytest.mark.unit


async def test_for_run_continues_seq_and_preloads_stored_views(monkeypatch):
    from app.db import session as dbs

    class _Res:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

        def scalar(self):
            return self._v

    class _S:
        async def execute(self, stmt, *a, **k):
            s = str(stmt.compile()).lower()
            if "max(" in s:
                return _Res(41)
            return _Res(
                {
                    "view": {"phase": "ended", "question": None, "step": 4},
                    "cost": {"spent_cents": 12.5},
                }
            )

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    w = await rr.RunEventWriter.for_run(7)
    assert w.run_id == 7 and w.seq == 41
    assert w.views["view"]["phase"] == "ended" and w.views["view"]["step"] == 4
    assert w.views["view"]["revision"] == 0  # unknown keys still come from empty_views
    assert w.views["cost"]["spent_cents"] == 12.5


async def test_for_run_on_a_run_with_no_events_starts_at_zero(monkeypatch):
    from app.db import session as dbs

    class _Res:
        def scalar_one_or_none(self):
            return None

        def scalar(self):
            return None

    class _S:
        async def execute(self, stmt, *a, **k):
            return _Res()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    w = await rr.RunEventWriter.for_run(8)
    assert w.seq == 0 and w.views == rr.empty_views()
