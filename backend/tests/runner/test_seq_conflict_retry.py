"""两个 writer 抢同一个 run 的 seq，谁都不该吃掉谁的事件（T8c 修复轮 1）。

``agent_run_transcript_events`` 有 ``UNIQUE (run_id, seq)``（mig 397），而一个
run 上可以同时存在两个 writer：活 run 自己的 ``RunRecorder`` 与任何走
``RunEventWriter.for_run`` 的迟到写入（登记口、workforce 的 ``subagent_done``、
park 回答）。``for_run`` 用 ``max(seq)`` 播种，活 writer 的计数器**不知道**有人
取走了下一个号，于是它的下一条 insert 撞索引 —— 而 ``append`` 的失败分支只记一
条 WARNING 就返回 ``None``，那条事件**从 transcript 里永久消失**。

真栈证据（run 348401200407189）：``… deliverable(4) tool_call(5) …``。序号没有
断层，恰恰是被吞掉的证据 —— 外部 writer 读到 max=3 写了 4，活 writer 也要 4、
撞掉、把自己的计数器推到 4，下一条才拿 5。中间那条事件不在库里。

此前唯一的安全网是 ``_unpersisted_subagent`` 重放，**只收两个 subagent 事件
类型**：`tool_call` / `assistant` / `deliverable` / `turn_end` 撞上就是真丢。

修法在根上：``append`` 撞唯一索引时**从库里重新播种计数器并重试**（有界）。
两个 writer 都走同一个 ``append``，所以两边同时得到修复；``_unpersisted_subagent``
保留为第二道网，但不再是唯一的那道。
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.ai.runner import run_recorder as rr

pytestmark = pytest.mark.unit


class _UniqueSeqDb:
    """``agent_run_transcript_events`` 的最小模型 —— **带唯一索引**。

    桩不模拟索引，这个缺陷就测不出来：它整个是索引触发的。
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.max_seq_reads = 0

    def execute(self, stmt):
        text = str(stmt).lstrip().upper().replace("PUBLIC.", "")
        if text.startswith("INSERT INTO AGENT_RUN_TRANSCRIPT_EVENTS"):
            params = stmt.compile().params
            key = (params["run_id"], params["seq"])
            if any((r["run_id"], r["seq"]) == key for r in self.rows):
                # ⚠️ 必须带 ``sqlstate`` 与 ``constraint_name`` —— 实现读的是
                # **驱动给的原始错误**，不是消息文本。抛一个裸 Exception 的桩
                # 会让重试分支永不命中，测试却仍然「红得像修好了」。
                raise _integrity(
                    "23505",
                    "duplicate key value violates unique constraint "
                    '"agent_run_transcript_events_run_id_seq_key"',
                    constraint="agent_run_transcript_events_run_id_seq_key",
                )
            self.rows.append(
                {
                    "run_id": params["run_id"],
                    "seq": params["seq"],
                    "event_type": params["event_type"],
                    "payload": params["payload"],
                }
            )
            return _Result([])
        if text.startswith("SELECT MAX("):
            self.max_seq_reads += 1
            return _Result([], scalar=max((r["seq"] for r in self.rows), default=None))
        if text.startswith("SELECT AGENT_RUNS.METADATA_JSON"):
            return _Result([], scalar={})
        if text.startswith("SELECT AGENT_RUN_TRANSCRIPT_EVENTS.EVENT_TYPE"):
            return _Result(
                [
                    {"event_type": r["event_type"], "payload": r["payload"]}
                    for r in sorted(self.rows, key=lambda x: x["seq"])
                    if r["event_type"] in rr.EXTERNALLY_WRITTEN_EVENT_TYPES
                ]
            )
        return _Result([])


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


def _wire(monkeypatch, db: _UniqueSeqDb) -> None:
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            return db.execute(stmt)

    @contextlib.asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)


async def test_two_writers_interleaving_lose_nothing(monkeypatch):
    """外部 writer 取走下一个号之后，活 writer 的事件仍然落库 —— 序号唯一、
    无空洞，而且**条数对得上**。"""
    db = _UniqueSeqDb()
    _wire(monkeypatch, db)

    live = rr.RunEventWriter(7, seq_start=0)
    assert await live.append("user", {"content": "go"}) == 1
    assert await live.append("step_start", {"turn": 1, "step": 1}) == 2

    # 登记口的迟到写入：另开一个 writer，从 max(seq) 播种 → 拿到 3。
    external = await rr.event_writer_for_run(7)
    assert await external.append("deliverable", _DELIVERABLE) == 3

    # 活 writer 还以为下一个是 3。撞索引之后必须重新播种并落到 4，
    # 而不是把这条事件丢掉。
    seq = await live.append("tool_call", {"name": "UpdateShot"})
    assert seq == 4, "the live writer's event was eaten by the seq collision"

    assert [r["seq"] for r in db.rows] == [1, 2, 3, 4]
    assert [r["event_type"] for r in db.rows] == [
        "user",
        "step_start",
        "deliverable",
        "tool_call",
    ]


async def test_the_external_writer_is_fixed_by_the_same_code_path(monkeypatch):
    """反向也要成立：活 writer 先占了号，迟到的那个 writer 不许丢事件。

    ``for_run`` 播种之后活 run 又写了两条 —— 登记口此时才落事件。
    """
    db = _UniqueSeqDb()
    _wire(monkeypatch, db)

    live = rr.RunEventWriter(7, seq_start=0)
    await live.append("user", {"content": "go"})

    external = await rr.event_writer_for_run(7)  # seeds at 1
    await live.append("step_start", {"turn": 1, "step": 1})  # takes 2
    await live.append("step_end", {"turn": 1, "step": 1})  # takes 3

    seq = await external.append("deliverable", _DELIVERABLE)
    assert seq == 4
    assert len(db.rows) == 4
    assert len({(r["run_id"], r["seq"]) for r in db.rows}) == 4


async def test_a_collision_opens_the_refold_guard(monkeypatch):
    """撞过一次 = **有第二个 writer 在这个 run 上**，这是内存里唯一拿得到的
    那个信号。重折守卫必须据此打开，否则外部写入的 ``view.outputs`` 会被下一
    次整值镜像抹掉 —— 活 recorder 自己一件产出都没折过的 run（只调
    ``GenerateShotImage`` 的那一轮）正是这个形状。"""
    db = _UniqueSeqDb()
    _wire(monkeypatch, db)

    live = rr.RunEventWriter(7, seq_start=0)
    await live.append("step_start", {"turn": 1, "step": 1})

    external = await rr.event_writer_for_run(7)
    await external.append("deliverable", _DELIVERABLE)

    # 活 writer 内存里没有任何产出，守卫本来是关的。
    assert "outputs" not in live.views["view"]
    await live.append("step_end", {"turn": 1, "step": 1, "cost_cents": 1.0})

    outputs = live.mirror_keys()["view"].get("outputs")
    assert outputs is not None, "the collision signal did not open the refold"
    assert outputs["total"] == 1


async def test_a_dead_database_still_takes_the_old_path(monkeypatch):
    """库挂了照旧走「折内存、不镜像、返回 None」—— 那条契约（transcript 没有
    的事件，镜像绝不声称有）没变。"""
    writer = _writer_against(monkeypatch, RuntimeError("pg is down"))
    assert await writer.append("user", {"content": "go"}) is None


async def test_a_non_unique_integrity_error_is_not_retried(monkeypatch):
    """**只有 `(run_id, seq)` 的唯一违规才是 seq 竞争。**

    同一张表上还有 `event_type` 的 CHECK 白名单、`run_id` 的 FK、三处 NOT
    NULL —— 这些违规同样是 `IntegrityError`。把它们当成竞争处理有三个真实代价：
    刷三条「另一个 writer 在这个 run 上」的 WARNING（**诊断是假的**，会把下一
    个人送去找一个不存在的第二 writer）、多付两次重播种读与两次注定失败的
    insert、以及把 `foreign_writer_seen` 永久抬成真，让重折守卫此后每次 append
    都多付一次读。

    典型触发者是接线 bug：新加了事件类型却忘了进 mig 的 CHECK 白名单
    （本仓 mig 285→397 正栽过这一类）。
    """
    calls: list = []
    writer = _writer_against(
        monkeypatch,
        _integrity("23514", 'new row violates check constraint "ck_event_type"'),
        calls=calls,
    )

    assert await writer.append("weird_new_type", {"x": 1}) is None
    # 一次 insert，不重试；没有重播种读。
    assert len([c for c in calls if c.startswith("INSERT")]) == 1
    assert [c for c in calls if c.startswith("SELECT")] == []
    assert writer.foreign_writer_seen is False


async def test_a_non_unique_integrity_error_never_opens_the_refold_guard(monkeypatch):
    """守卫开着的代价是这个 run 剩下的每次 append 各一次读。一个 CHECK 违规
    不该买单，因为它根本不意味着有第二个 writer。"""
    calls: list = []
    writer = _writer_against(
        monkeypatch, _integrity("23503", "violates foreign key constraint"), calls=calls
    )
    await writer.append("tool_call", {"name": "X"})

    calls.clear()
    await writer.refold_external_slices()
    assert calls == [], f"the guard opened on a non-conflict failure: {calls}"


async def test_a_unique_violation_on_another_index_is_not_a_seq_race(monkeypatch):
    """23505 是必要条件，不是充分条件。驱动报出**哪个**约束时就用它排除 ——
    别的唯一索引违规同样不意味着有人抢了我的号。

    ⚠️ 驱动**不报**约束名时不算否定证据（别的驱动、被包了一层的错误），那时
    由 sqlstate 定夺 —— 见 ``_is_seq_conflict`` 的 docstring。
    """
    calls: list = []
    writer = _writer_against(
        monkeypatch,
        _integrity(
            "23505",
            'duplicate key value violates unique constraint "uq_something_else"',
            constraint="uq_something_else",
        ),
        calls=calls,
    )

    assert await writer.append("user", {"content": "go"}) is None
    assert [c for c in calls if c.startswith("SELECT")] == []
    assert writer.foreign_writer_seen is False


def _integrity(
    sqlstate: str, message: str, *, constraint: str | None = None
) -> IntegrityError:
    """SQLAlchemy 包着 asyncpg 原始异常的真实形状：``orig`` 带 ``sqlstate``，
    唯一违规还带 ``constraint_name``。实现读的正是这两个属性。"""

    class _Orig(Exception):
        def __init__(self) -> None:
            super().__init__(message)
            self.sqlstate = sqlstate
            self.constraint_name = constraint

    return IntegrityError("INSERT", {}, _Orig())


def _writer_against(monkeypatch, error: Exception, *, calls: list | None = None):
    """每次 execute 都抛同一个错误的 writer。``calls`` 记下语句种类，用来断言
    「重试了几次」与「重播种读了没有」。"""
    from app.db import session as dbs

    class _S:
        async def execute(self, stmt, *a, **k):
            if calls is not None:
                calls.append(str(stmt).lstrip().upper().replace("PUBLIC.", "")[:6])
            raise error

    @contextlib.asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)
    return rr.RunEventWriter(7, seq_start=0)


_DELIVERABLE = {
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
