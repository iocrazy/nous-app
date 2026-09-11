"""``view.outputs`` survives the live recorder's next mirror (三期 3a T8c 缺陷 1).

Same shape as ``test_children_refold_from_transcript`` and the same root
cause: TWO writers touch one run. ``register_deliverable`` falls back to
``RunEventWriter.for_run`` when no recorder is handed to it, so the
``deliverable`` event folds into a SECOND writer's views; the live run's own
recorder then mirrors its WHOLE ``view`` and erases the outputs slice.

真栈证据（run 348401200407189）：transcript 里有 ``deliverable(seq 4)``，
``/runs/{id}/view-at`` 现折出 ``{"total":1,…}``，而
``agent_runs.metadata_json->'view'`` 的 17 个键里**没有** ``outputs`` ——
Cockpit 那一格唯一的数据源永远是空的。

修法两层，本文件钉第二层：

1. **活路径把自己的 recorder 交给登记口**（主修，接线由
   ``tests/services/deliverables/test_live_recorder_wiring.py`` 钉住）。一个
   writer 就没有覆盖，也没有 seq 相撞。
2. **镜像前从 transcript 重折 outputs**（本文件）。迟到的登记 —— DBOS 出图、
   父 run 还活着时另一条路写进来的 —— 仍然会经 ``for_run`` 落事件，重折让活
   recorder 的下一次镜像把它捡回来而不是抹掉。

⚠️ 这里刻意**不**模拟 ``(run_id, seq)`` 唯一索引：两个 writer 各记各的 seq
计数器，生产上活 recorder 的下一条 insert 真的会撞 seq 并被丢掉（同一个缺陷
的另一半，由第 1 层关掉）。模拟它只会让本文件测不到想测的东西。
"""

from __future__ import annotations

import contextlib

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = pytest.mark.unit

DELIVERABLE = {
    "kind": "script_shot",
    "ref_id": "337650953731886",
    "version": 1,
    "parent_version": None,
    "title": "MEDIUM",
    "model": None,
    "cost_cents": None,
    "turn": 1,
    "step": 1,
}


class _Result:
    def __init__(self, rows, scalar=None):
        self._rows, self._scalar = rows, scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDb:
    """One run's transcript plus its ``agent_runs`` row, in memory."""

    def __init__(self, metadata=None):
        self.events: list[dict] = []
        self.metadata = metadata if metadata is not None else {}

    def execute(self, stmt):
        # ⚠️ 表名带 ``public.`` schema 前缀（``SELECT public.agent_runs…``）。
        # 摘掉它再比，否则每个分支都静默不命中，桩库看起来永远是空的。
        text = str(stmt).lstrip().upper().replace("PUBLIC.", "")
        if text.startswith("INSERT INTO AGENT_RUN_TRANSCRIPT_EVENTS"):
            params = stmt.compile().params
            self.events.append(
                {
                    "seq": params["seq"],
                    "event_type": params["event_type"],
                    "payload": params["payload"],
                }
            )
            return _Result([])
        if text.startswith("SELECT MAX("):
            return _Result(
                [], scalar=max((e["seq"] for e in self.events), default=None)
            )
        if text.startswith("SELECT AGENT_RUNS.METADATA_JSON"):
            return _Result([], scalar=self.metadata)
        if text.startswith("SELECT AGENT_RUN_TRANSCRIPT_EVENTS.EVENT_TYPE"):
            rows = [
                {"event_type": e["event_type"], "payload": e["payload"]}
                for e in sorted(self.events, key=lambda r: r["seq"])
                if e["event_type"] in rr.EXTERNALLY_WRITTEN_EVENT_TYPES
            ]
            return _Result(rows)
        return _Result([])


def _wire(monkeypatch, db: _FakeDb, *, reads: list | None = None) -> None:
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            if reads is not None and str(stmt).lstrip().upper().startswith("SELECT"):
                reads.append(str(stmt))
            return db.execute(stmt)

    @contextlib.asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)


async def test_a_late_deliverable_survives_the_live_recorders_next_mirror(
    monkeypatch,
):
    """活 recorder 自己折过一件产出，随后**另一个** writer 又登记了一件
    （迟到的 DBOS 出图就是这个形状）—— 活 recorder 的下一次镜像必须把两件
    都带上，而不是用自己内存里那份一件的覆盖回去。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    await rec.record_event("step_start", {"turn": 1, "step": 1})
    # 主修之后登记口拿到的就是这个 recorder，事件走它自己的 append。
    await rec.record_event("deliverable", dict(DELIVERABLE), turn=1, step=1)
    assert rec._writer().views["view"]["outputs"]["total"] == 1

    external = await rr.event_writer_for_run(7)
    await external.append(
        "deliverable",
        {**DELIVERABLE, "ref_id": "337650953731999", "title": "WIDE"},
        turn=1,
        step=1,
    )

    await rec.record_event(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 5.0, "model": "m"}
    )

    outputs = rec._writer().mirror_keys()["view"].get("outputs")
    assert outputs is not None, "the live mirror dropped view.outputs entirely"
    assert (outputs["total"], outputs["revised"]) == (2, 0)
    assert outputs["last"]["ref_id"] == "337650953731999"


async def test_a_late_revision_moves_the_counts_the_cockpit_reads(monkeypatch):
    """修订计数也要跟着走 —— Cockpit 那一格读的正是 ``total`` / ``revised``。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    await rec.record_event("deliverable", dict(DELIVERABLE), turn=1, step=1)

    external = await rr.event_writer_for_run(7)
    await external.append(
        "deliverable",
        {**DELIVERABLE, "version": 2, "parent_version": 1},
        turn=1,
        step=1,
    )

    # 任何**改动 view** 的后续事件都会带出一次镜像；不改 view 的事件走
    # ``_fold_and_mirror`` 的 early-return，压根不写库（既有行为）。
    await rec.record_event(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 5.0, "model": "m"}
    )

    outputs = rec._writer().mirror_keys()["view"]["outputs"]
    assert (outputs["total"], outputs["revised"]) == (2, 1)
    assert outputs["last"]["version"] == 2


async def test_a_run_with_neither_children_nor_outputs_never_queries(monkeypatch):
    """无子代理、无产出的 run 一次 SELECT 都不该付 —— 压倒多数的 run 是这种。"""
    reads: list = []
    _wire(monkeypatch, _FakeDb(), reads=reads)

    writer = rr.RunEventWriter(7, seq_start=0)
    await writer.append(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 1.0, "model": "m"}
    )

    assert reads == [], f"unexpected transcript read: {reads}"


async def test_a_transcript_without_deliverables_never_wipes_a_folded_outputs(
    monkeypatch,
):
    """重折只**补**不抹：事件日志里一条 ``deliverable`` 都没有时，内存里已经
    折出来的那份必须原样留着（插入失败的那条事件正是这种形状）。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    writer = rr.RunEventWriter(7, seq_start=0)
    writer.views["view"]["outputs"] = {
        "total": 1,
        "revised": 0,
        "last": {"kind": "script_shot", "ref_id": "1", "version": 1, "title": "t"},
        "seen": ["script_shot:1:1"],
    }

    await writer.refold_external_slices()

    assert writer.views["view"]["outputs"]["total"] == 1
