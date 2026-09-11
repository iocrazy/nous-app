"""两个并发 enqueue 同一个 dedupe_key：必须只落一行，且两个调用方都拿到它。

查-插之间有窗口，所以在 462 之前两个**同时**到达的写手会双双 miss 然后各插
一行——偶发、无法在单测里复现。462 的 ``agent_run_inbox_dedupe_live_key``
让第二个写手撞上 IntegrityError，这里用「两次查都 miss + 第二次 insert 抛
冲突」的桩把那条并发路径变成必然，钉住 enqueue 的回读复用分支。

⚠️ 同时钉住反向：不是这场竞态的 IntegrityError（比如 kind CHECK 拒绝）必须
原样抛出去。把任何冲突都读成「对方赢了」会把真缺陷变成静默的错行复用。
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from app.repositories import agent_run_inbox_repository as repo

pytestmark = pytest.mark.unit

DEDUPE_KEY = "sched:s1:t0"


class _Row:
    """Stands in for an AgentRunInbox ORM instance (``_row`` reads columns)."""

    def __init__(self, row_id: int) -> None:
        from app.models import AgentRunInbox

        self.__table__ = AgentRunInbox.__table__
        for column in AgentRunInbox.__table__.columns:
            setattr(self, column.key, None)
        self.id = row_id
        self.target_kind = "issue"
        self.target_id = 5
        self.kind = "steer"
        self.content = {"text": "wake", "dedupe_key": DEDUPE_KEY}
        self.created_at = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalar_one(self):
        return self._rows[0]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _conflict() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO public.agent_run_inbox …",
        {},
        Exception(
            "duplicate key value violates unique constraint "
            '"agent_run_inbox_dedupe_live_key"'
        ),
    )


class _Session:
    """Replays a script of results; an ``Exception`` entry is raised instead."""

    def __init__(self, script: list[Any]) -> None:
        self.statements: list = []
        self._script = list(script)

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        nxt = self._script.pop(0) if self._script else _Result([])
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _patch(monkeypatch, session: _Session) -> None:
    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(repo, "write_scope", _scope)
    monkeypatch.setattr(repo, "read_scope", _scope)


async def _enqueue(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "target_kind": "issue",
        "target_id": 5,
        "user_id": "u1",
        "kind": "steer",
        "content": {"text": "wake"},
        "dedupe_key": DEDUPE_KEY,
    }
    kwargs.update(overrides)
    return await repo.AgentRunInboxRepository().enqueue(**kwargs)


async def test_two_simultaneous_enqueues_yield_one_row(monkeypatch):
    session = _Session(
        [
            _Result([]),  # 写手 A 查：没有
            _Result([_Row(1)]),  # 写手 A 插：赢
            _Result([]),  # 写手 B 查：也没有（同时发生）
            _conflict(),  # 写手 B 插：唯一索引挡下
            _Result([_Row(1)]),  # 写手 B 回读：拿到 A 那一行
        ]
    )
    _patch(monkeypatch, session)

    first = await _enqueue()
    second = await _enqueue()

    assert first["id"] == second["id"] == 1
    assert len(session.statements) == 5


async def test_a_conflict_nobody_can_explain_is_re_raised(monkeypatch):
    """回读拿不到赢家 → 这不是 dedupe 竞态，是别的约束在拒绝这次写入。"""
    session = _Session([_Result([]), _conflict(), _Result([])])
    _patch(monkeypatch, session)

    with pytest.raises(IntegrityError):
        await _enqueue()


async def test_under_an_ambient_unit_of_work_the_doomed_re_read_is_not_attempted(
    monkeypatch,
):
    """UoW 下这次冲突已经把**调用方的**事务弄废了：回读会落在同一个 abort 掉
    的 session 上抛 PendingRollbackError。那次查询是注定失败的，不是第二个值
    得诊断的故障——所以根本不该发起，日志里更不该出现「re-read failed too」
    这种把人引向连接/数据库的句子。照 AssetsRepository.create 的先例
    （判据 app/db/session.py::in_unit_of_work）。

    桩里第三步真的抛 PendingRollbackError，这样两条断言各自都有牙：拿掉守卫
    会同时多出一条语句和一行误导日志。
    """
    from loguru import logger as loguru_logger

    from app.db import session as session_mod

    session = _Session(
        [
            _Result([]),  # 查：没有
            _conflict(),  # 插：唯一索引挡下，调用方的事务就此作废
            PendingRollbackError(  # 回读若真发起，只会撞上这个
                "Can't reconnect until invalid transaction is rolled back."
            ),
        ]
    )
    _patch(monkeypatch, session)
    seen: list[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="DEBUG")
    token = session_mod._request_session.set(object())
    try:
        with pytest.raises(IntegrityError):
            await _enqueue()
    finally:
        session_mod._request_session.reset(token)
        loguru_logger.remove(handler_id)

    assert len(session.statements) == 2, "注定失败的回读根本不该发起"
    assert not [line for line in seen if "re-read failed" in line], seen
    assert not [line for line in seen if "lost the race" in line], seen


async def test_without_a_key_a_conflict_is_never_reinterpreted(monkeypatch):
    """没有 dedupe_key 的插入不可能撞那个索引，所以一次冲突就是一次冲突。"""
    session = _Session([_conflict()])
    _patch(monkeypatch, session)

    with pytest.raises(IntegrityError):
        await _enqueue(dedupe_key=None)

    assert len(session.statements) == 1, "不该为无 key 的插入去回读"
