"""Review round 2: a child that finishes while the parent is STILL running.

Two writers touch one run's ``children`` / ``cost.by_child``: the parent's own
recorder (in-memory views, mirrored as WHOLE values) and the worker, which
appends ``subagent_done`` through a separate ``RunEventWriter.for_run``. The
parent's next mirror used to overwrite the worker's contribution wholesale —
so a concurrent child left ``async_pending`` stuck at +1 and its cost out of
the parent's total.

The persisted event log is the tie-breaker: both writers APPEND, so re-folding
those two slices from the transcript before every write is race-free whatever
the interleaving.
"""

from __future__ import annotations

import contextlib

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = pytest.mark.unit

SPAWN_ASYNC = (
    "subagent_spawned",
    {
        "child_run_id": None,
        "task_id": "t1",
        "mode": "async",
        "subagent_type": "librarian",
        "description": "d",
    },
)
DONE_ASYNC = (
    "subagent_done",
    {
        "child_run_id": "52",
        "task_id": "t1",
        "mode": "async",
        "status": "success",
        "cost_cents": 4.0,
        "tokens_used": 9,
    },
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def _wire_db(monkeypatch, transcript, *, reads=None):
    """``transcript`` is the list the SELECT returns; ``reads`` counts them."""
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            if reads is not None:
                reads.append(str(stmt))
            return _Rows(list(transcript))

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    monkeypatch.setattr(dbs, "write_scope", _ws)


async def test_a_child_done_between_two_parent_mirrors_survives(monkeypatch):
    """The worker's done landed in the transcript while the parent was mid
    turn. The parent's next mirror must carry it, not erase it."""
    writer = rr.RunEventWriter(7, seq_start=0)
    # ``append`` inserts the row BEFORE folding, so the transcript already
    # holds the spawn by the time the mirror re-reads it. The stub models
    # that ordering; a stub that returned nothing here would be describing a
    # database the code cannot produce.
    _wire_db(monkeypatch, [{"event_type": SPAWN_ASYNC[0], "payload": SPAWN_ASYNC[1]}])

    # The parent announces a background child.
    await writer.append(*SPAWN_ASYNC)
    assert writer.views["view"]["children"]["async_pending"] == 1

    # The worker appends the done through its OWN writer — the parent's
    # in-memory views know nothing about it.
    _wire_db(
        monkeypatch,
        [
            {"event_type": SPAWN_ASYNC[0], "payload": SPAWN_ASYNC[1]},
            {"event_type": DONE_ASYNC[0], "payload": DONE_ASYNC[1]},
        ],
    )

    # Any later event on the parent mirrors whole values.
    await writer.append(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 10.0, "model": "m"}
    )

    children = writer.views["view"]["children"]
    assert children["done"] == 1 and children["async_pending"] == 0
    assert children["total"] == 1
    assert writer.views["cost"]["by_child"] == {"52": 4.0}
    assert writer.views["cost"]["own_cents"] == 10.0
    assert writer.views["cost"]["spent_cents"] == 14.0


async def test_a_parent_finishing_after_a_mid_run_child_bills_the_child(
    monkeypatch,
):
    """``_finish`` reads the in-memory views; without the re-fold it wrote an
    own-only ``cost_cents`` and the child's spend vanished from the column."""
    captured: dict = {}

    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            compiled = stmt.compile()
            params = compiled.params or {}
            if "cost_cents" in params:
                captured["cost_cents"] = params["cost_cents"]
            return _Rows(
                [
                    {"event_type": SPAWN_ASYNC[0], "payload": SPAWN_ASYNC[1]},
                    {"event_type": DONE_ASYNC[0], "payload": DONE_ASYNC[1]},
                ]
            )

    @contextlib.asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    rec._prompt_rate = 1.0
    rec._completion_rate = 1.0
    rec.record_usage(prompt_tokens=5000, completion_tokens=5000)  # 10.0 cents
    writer = rec._writer()
    # The parent announced the child; the worker's done is only in the
    # transcript.
    writer.views["view"]["children"] = {
        "total": 1,
        "done": 0,
        "running": 0,
        "async_pending": 1,
        "last": None,
    }

    await rec._finish(status="completed")

    assert captured["cost_cents"] == 14.0
    assert writer.views["view"]["children"]["async_pending"] == 0
    assert writer.views["cost"]["by_child"] == {"52": 4.0}


async def test_a_run_with_no_children_never_queries_the_transcript(monkeypatch):
    """Zero cost for the overwhelming majority of runs."""
    reads: list = []
    _wire_db(monkeypatch, [], reads=reads)

    writer = rr.RunEventWriter(7, seq_start=0)
    await writer.append(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 10.0, "model": "m"}
    )

    assert writer.views["view"]["children"]["total"] == 0
    selects = [s for s in reads if s.lstrip().upper().startswith("SELECT")]
    assert selects == [], f"unexpected transcript read: {selects}"


async def test_an_event_that_failed_to_persist_is_still_counted(monkeypatch):
    """The insert-failure branch folds in memory WITHOUT a row. Re-folding
    purely from the transcript would drop it, so the union is what gets
    folded."""
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            return _Rows([])

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    @contextlib.asynccontextmanager
    async def _ws():
        raise RuntimeError("insert exploded")
        yield  # pragma: no cover

    monkeypatch.setattr(dbs, "read_scope", _rs)
    monkeypatch.setattr(dbs, "write_scope", _ws)

    writer = rr.RunEventWriter(7, seq_start=0)
    assert await writer.append(*SPAWN_ASYNC) is None
    assert writer.views["view"]["children"]["async_pending"] == 1

    await writer.refold_children()
    assert writer.views["view"]["children"]["async_pending"] == 1
    assert writer.views["view"]["children"]["total"] == 1


async def test_a_failed_transcript_read_leaves_the_slices_alone(monkeypatch):
    """Degrade to what we have. Wiping children because a read blipped would
    be worse than a stale count."""
    from app.db import session as dbs

    @contextlib.asynccontextmanager
    async def _rs():
        raise RuntimeError("pg is down")
        yield  # pragma: no cover

    monkeypatch.setattr(dbs, "read_scope", _rs)

    writer = rr.RunEventWriter(7, seq_start=0)
    writer.views["view"]["children"] = {
        "total": 2,
        "done": 1,
        "running": 0,
        "async_pending": 1,
        "last": None,
    }
    writer.views["cost"]["by_child"] = {"51": 3.0}

    await writer.refold_children()

    assert writer.views["view"]["children"]["total"] == 2
    assert writer.views["cost"]["by_child"] == {"51": 3.0}
