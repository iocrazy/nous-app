"""两条 BYOK 道必须跟着它们的本体一起过 ``refold_external_slices``（修复轮 1 C1）。

同一个缺陷的第三、第四个分量。``by_child`` / ``media_cents`` 早就在重折里被
抬回来了（见 ``test_children_refold_from_transcript`` 与
``test_outputs_refold_from_transcript`` 的 docstring：两个 writer 摸同一条 run，
活 recorder 的下一次镜像会把另一个 writer 折出来的那半边抹掉）。BYOK 半边
是同一次 fold 的产物 —— scratch 里数据是全的，只是没往回拷。

漏掉的代价**完全静默**。⚠️ 注意口径已经变过一次（终审 I3）：**当前扣费按「行」
聚合**，只读每条 run 自己的 ``own_cents`` / ``media_cents`` 减各自的 BYOK 道，
``by_child`` 与 ``by_child_byok`` 都**不参与**（见 ``ai/billing/tree_charge.py``
模块 docstring）。所以今天两条道的代价各不相同：

* ``media_cents`` / ``media_byok_cents`` —— **真的是钱**。它们是本行自己的分量，
  收口逐行读它们，少抬 BYOK 半边就是按平台价收一笔用户自己付过的账。
* ``by_child`` / ``by_child_byok`` —— **当前无消费方**，只影响父行面板上的分解；
  异步子 agent 的 BYOK 由它自己那一行报。两条道仍必须一起抬，因为它们是同一次
  fold 的两半，只抬一半会让父行的分解自相矛盾（而异步链将来若改回按 ``by_child``
  聚合，它就直接回到钱上）。

两条道少的恰好是**异步子 agent 的整棵子树**与**经 ``for_run`` writer 登记的产出**
—— 也就是生产上最常见的那两种。

⚠️ 这里刻意不模拟 ``(run_id, seq)`` 唯一索引，理由与
``test_outputs_refold_from_transcript`` 顶部那条逐字相同。
"""

from __future__ import annotations

import contextlib

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = pytest.mark.unit

#: BYOK 出的图：``cost_cents`` 照算（血缘里那是真价），同时带 BYOK 标记。
BYOK_DELIVERABLE = {
    "kind": "generated_media",
    "ref_id": "337650953731886",
    "version": 1,
    "parent_version": None,
    "title": "a cat",
    "model": "doubao-seedream-4-0",
    "cost_cents": 12.0,
    "byok_cents": 12.0,
    "turn": 1,
    "step": 1,
}

#: 活 recorder 自己折过的一件平台产出。它的存在是重折**发生**的前提 ——
#: 既有的 early-return 让「无子代理、无产出」的 run 一次 SELECT 都不付，所以
#: 一条什么都没折过的 run 根本不会走到本文件要测的那段。
LIVE_DELIVERABLE = {
    "kind": "generated_media",
    "ref_id": "337650953700001",
    "version": 1,
    "parent_version": None,
    "title": "platform",
    "model": "doubao-seedream-4-0",
    "cost_cents": 3.0,
    "turn": 1,
    "step": 1,
}

#: 异步子 agent：worker 在父 run 收工后才写这条，永远不经活 recorder。
BYOK_SUBAGENT_DONE = {
    "child_run_id": "52",
    "mode": "async",
    "subagent_type": "summarize",
    "status": "success",
    "cost_cents": 9.0,
    "byok_cents": 6.0,
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
        # ⚠️ 表名带 ``public.`` schema 前缀，摘掉再比 —— 否则每个分支都静默
        # 不命中，桩库看起来永远是空的。
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


def _wire(monkeypatch, db: _FakeDb) -> None:
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            return db.execute(stmt)

    @contextlib.asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)


async def test_both_byok_lanes_come_back_from_the_transcript(monkeypatch):
    """两条**外部 writer** 写的事件（迟到的登记 + 异步子 agent），活 recorder
    一条都没折过。重折必须把本体和 BYOK 半边一起抬回来。

    只抬本体的代价按道不同（终审 I3）：``media_byok_cents`` 那条是**真钱**（收口
    逐行读本行的 media 两道）；``by_child_byok`` 那条当前无消费方，只让父行面板的
    分解自相矛盾 —— 但两条都是同一次 fold 的两半，要抬一起抬。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    await rec.record_event("deliverable", dict(LIVE_DELIVERABLE), turn=1, step=1)

    external = await rr.event_writer_for_run(7)
    await external.append("deliverable", dict(BYOK_DELIVERABLE), turn=1, step=1)
    await external.append("subagent_done", dict(BYOK_SUBAGENT_DONE), turn=1, step=1)

    # 任何改动 view 的后续事件都会带出一次镜像，重折挂在那上面。
    await rec.record_event(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 5.0, "model": "m"}
    )

    cost = rec._writer().views["cost"]
    # 先钉本体确实被抬了（它们是既有行为；本体没抬说明探针根本没跑到）。
    assert cost["media_cents"] == 15.0  # 3 平台 + 12 BYOK
    assert cost["by_child"] == {"52": 9.0}
    # BYOK 半边 —— 本轮修的就是这两行。
    assert cost["media_byok_cents"] == 12.0
    assert cost["by_child_byok"] == {"52": 6.0}


async def test_the_byok_lanes_ride_the_same_mirror_the_totals_do(monkeypatch):
    """镜像进 ``agent_runs.metadata_json`` 的那份 ``cost`` 也要带着两条 BYOK 道 ——
    ``_finish`` 之后的读方（Task 3 的分桶）读的是落库那份，不是内存那份。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    await rec.record_event("deliverable", dict(LIVE_DELIVERABLE), turn=1, step=1)
    external = await rr.event_writer_for_run(7)
    await external.append("deliverable", dict(BYOK_DELIVERABLE), turn=1, step=1)
    await external.append("subagent_done", dict(BYOK_SUBAGENT_DONE), turn=1, step=1)

    await rec.record_event(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 5.0, "model": "m"}
    )

    mirrored = rec._writer().mirror_keys()["cost"]
    assert mirrored["media_byok_cents"] == 12.0
    assert mirrored["by_child_byok"] == {"52": 6.0}
    # BYOK 不进 spent_cents：5 own + 9 子 + 15 媒体。
    assert mirrored["spent_cents"] == 29.0


async def test_a_transcript_with_no_byok_leaves_the_lanes_at_their_empty_shape(
    monkeypatch,
):
    """全平台的 run 重折后两条道仍是空的形状（``0.0`` / ``{}``），不是 ``None``
    也不是缺键 —— 读方按六个数分桶，缺键会让它去分辨「没花」与「没有这个字段」。"""
    db = _FakeDb()
    _wire(monkeypatch, db)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    await rec.record_event("deliverable", dict(LIVE_DELIVERABLE), turn=1, step=1)
    external = await rr.event_writer_for_run(7)
    await external.append(
        "deliverable",
        {k: v for k, v in BYOK_DELIVERABLE.items() if k != "byok_cents"},
        turn=1,
        step=1,
    )
    await external.append(
        "subagent_done",
        {k: v for k, v in BYOK_SUBAGENT_DONE.items() if k != "byok_cents"},
        turn=1,
        step=1,
    )

    await rec.record_event(
        "step_end", {"turn": 1, "step": 1, "cost_cents": 5.0, "model": "m"}
    )

    cost = rec._writer().views["cost"]
    assert cost["media_cents"] == 15.0
    assert cost["media_byok_cents"] == 0.0
    assert cost["by_child_byok"] == {}
