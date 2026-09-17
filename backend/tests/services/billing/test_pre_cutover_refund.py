"""退款脚本选谁、退多少、写不写。

2026-09-17 事故：「谁把树收口谁扣费」上线首轮，清扫器把 43 棵**上线前**的历史树
收口并扣了 87 分。这里钉住把钱退回去的那一半 —— 尤其是**两个方向的边界**，任何一
侧写错都会让退款本身变成第二次事故：

* 只按「切换点之后扣的」选 → 会把切换点之后**正常**收的钱也退掉；
* 只按「root 早于切换点」选 → 会把切换点之前旧口径逐 run 扣的钱也退掉，而那些是
  当时的正常收费。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.billing import pre_cutover_refund as mod

pytestmark = [pytest.mark.unit]

CUTOVER = datetime(2026, 9, 17, 6, 42, 56, tzinfo=timezone.utc)


def _txn(txn_id, root_id, points, *, created_at, team_id=310812366953241):
    """一条 consume 流水。``amount`` 是**负**的 —— 扣费的真实形状。"""
    return SimpleNamespace(
        id=txn_id,
        team_id=team_id,
        user_id=uuid4(),
        amount=-points,
        reference_id=str(root_id),
        created_at=created_at,
    )


def _root(run_id, started_at):
    return SimpleNamespace(id=run_id, started_at=started_at)


def _db(monkeypatch, *, txns, roots, write_sink=None):
    """两次读（流水、root 行）+ 若干次写（改戳）。"""

    class _Res:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _ReadSession:
        def __init__(self, rows):
            self._rows = rows

        async def execute(self, stmt):
            return _Res(self._rows)

    reads = [txns, roots]

    @asynccontextmanager
    async def _read():
        yield _ReadSession(reads.pop(0) if reads else [])

    class _WriteSession:
        async def execute(self, stmt):
            if write_sink is not None:
                write_sink.append(stmt)

    @asynccontextmanager
    async def _write():
        yield _WriteSession()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _read)
    monkeypatch.setattr(db_session, "write_scope", _write)


@pytest.fixture
def cutover(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "AGENT_POINTS_TREE_CUTOVER",
        CUTOVER.isoformat().replace("+00:00", "Z"),
    )
    return CUTOVER


@pytest.fixture
def refunds(monkeypatch):
    """拦 ``PointsService.refund_points``，返回「退成了」。"""
    mock = AsyncMock(
        return_value={"success": True, "new_balance": 100, "already_refunded": False}
    )
    import app.services.billing.points_service as ps

    monkeypatch.setattr(
        ps, "PointsService", lambda: SimpleNamespace(refund_points=mock)
    )
    return mock


def test_the_consume_query_only_asks_for_post_cutover_agent_run_charges():
    """⚠️ 这两个边界**都在 SQL 里**，桩 session 不会替你执行它们 —— 用「喂进去的行」
    断言等于自己骗自己（第一版就这么写的，切换点之前的那条流水照样被选中）。所以断
    编译出来的谓词。"""
    from sqlalchemy.dialects.postgresql import dialect

    compiled = mod._consume_rows_stmt(CUTOVER).compile(dialect=dialect())
    sql = str(compiled)
    assert "created_at >=" in sql, sql
    bound = set(compiled.params.values())
    assert {"consume", "agent_run", CUTOVER} <= bound, bound


def test_the_root_query_only_accepts_pre_cutover_root_rows():
    from sqlalchemy.dialects.postgresql import dialect

    compiled = mod._pre_cutover_roots_stmt([1, 2], CUTOVER).compile(dialect=dialect())
    sql = str(compiled)
    assert "parent_run_id IS NULL" in sql, sql
    assert "started_at <" in sql, sql
    # ⚠️ ``IN`` 的绑定是一个 list（不可哈希），不能整批塞进 set —— 只挑时间值比。
    assert CUTOVER in [v for v in compiled.params.values() if isinstance(v, datetime)]


async def test_a_charge_whose_root_is_not_pre_cutover_is_dropped(cutover, monkeypatch):
    """两次查询在 Python 侧的接缝：root 查询没返回的那条流水必须被丢掉。

    这一层是真的在 Python 里做的（``ref not in started_by_id``），所以桩能证明它。
    """
    _db(
        monkeypatch,
        txns=[
            _txn(1, 800000000000001, 52, created_at=cutover + timedelta(minutes=1)),
            _txn(2, 800000000000002, 3, created_at=cutover + timedelta(hours=2)),
        ],
        # 只有第一棵是切换点之前开始的 root；第二棵根本没回来。
        roots=[_root(800000000000001, cutover - timedelta(days=5))],
    )
    found = await mod.find_candidates(cutover)
    assert [c.transaction_id for c in found] == [1]
    assert found[0].points == 52
    assert found[0].root_run_id == 800000000000001


async def test_a_non_numeric_reference_id_is_skipped_not_guessed(cutover, monkeypatch):
    """``reference_id`` 是 varchar。非数字的不是 run id —— 跳过并记一条，不要猜。"""
    bad = _txn(9, 0, 5, created_at=cutover + timedelta(minutes=1))
    bad.reference_id = "not-an-id"
    _db(monkeypatch, txns=[bad], roots=[])
    assert await mod.find_candidates(cutover) == ()


async def test_the_dry_run_touches_nothing_but_reports_the_whole_bill(
    cutover, refunds, monkeypatch
):
    sink: list = []
    _db(
        monkeypatch,
        txns=[
            _txn(1, 800000000000001, 52, created_at=cutover + timedelta(minutes=1)),
            _txn(
                2,
                800000000000002,
                35,
                created_at=cutover + timedelta(minutes=2),
                team_id=331438215859255,
            ),
        ],
        roots=[
            _root(800000000000001, cutover - timedelta(days=5)),
            _root(800000000000002, cutover - timedelta(days=4)),
        ],
        write_sink=sink,
    )
    report = await mod.refund_pre_cutover_charges(dry_run=True)
    assert report.points_total == 87
    assert report.by_team == {310812366953241: 52, 331438215859255: 35}
    # dry run 的意思是一分钱都没动，戳也没改。
    refunds.assert_not_awaited()
    assert sink == []
    assert (report.refunded, report.points_refunded, report.stamped) == (0, 0, 0)


async def test_executing_refunds_each_row_and_rewrites_the_stamp(
    cutover, refunds, monkeypatch
):
    sink: list = []
    _db(
        monkeypatch,
        txns=[_txn(1, 800000000000001, 52, created_at=cutover + timedelta(minutes=1))],
        roots=[_root(800000000000001, cutover - timedelta(days=5))],
        write_sink=sink,
    )
    report = await mod.refund_pre_cutover_charges(dry_run=False)
    assert (report.refunded, report.points_refunded, report.stamped) == (1, 52, 1)
    kwargs = refunds.await_args.kwargs
    assert kwargs["amount"] == 52
    # reference_id 必须是 root id：退款的幂等索引就建在
    # (team_id, reference_type, reference_id) 上，写错就退不成幂等。
    assert kwargs["reference_id"] == "800000000000001"
    assert kwargs["reference_type"] == "agent_run"
    assert len(sink) == 1


async def test_a_row_refunded_before_is_counted_apart_from_a_fresh_refund(
    cutover, refunds, monkeypatch
):
    """重跑时 ``already`` 才是大头。把它算进 ``refunded`` 会让第二次运行看起来
    像又退了一遍钱 —— 两个相反的事实不许共用一个计数。"""
    refunds.return_value = {
        "success": True,
        "new_balance": 100,
        "already_refunded": True,
    }
    _db(
        monkeypatch,
        txns=[_txn(1, 800000000000001, 52, created_at=cutover + timedelta(minutes=1))],
        roots=[_root(800000000000001, cutover - timedelta(days=5))],
    )
    report = await mod.refund_pre_cutover_charges(dry_run=False)
    assert (report.already, report.refunded, report.points_refunded) == (1, 0, 0)


async def test_a_failed_refund_never_rewrites_the_stamp(cutover, refunds, monkeypatch):
    """退款失败还改戳 = 把这笔该退的证据抹掉，以后再也认不出来。"""
    refunds.return_value = {
        "success": False,
        "new_balance": None,
        "already_refunded": False,
    }
    sink: list = []
    _db(
        monkeypatch,
        txns=[_txn(1, 800000000000001, 52, created_at=cutover + timedelta(minutes=1))],
        roots=[_root(800000000000001, cutover - timedelta(days=5))],
        write_sink=sink,
    )
    report = await mod.refund_pre_cutover_charges(dry_run=False)
    assert (report.failed, report.refunded, report.stamped) == (1, 0, 0)
    assert sink == []


async def test_it_refuses_to_run_without_a_readable_cutover(monkeypatch):
    """没有切换点就没有「哪些是历史树」的答案。拒绝，而不是拿一个默认值去退钱。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_POINTS_TREE_CUTOVER", "not-a-timestamp")
    with pytest.raises(RuntimeError, match="AGENT_POINTS_TREE_CUTOVER"):
        await mod.refund_pre_cutover_charges(dry_run=True)


def test_the_stamp_rewrite_drops_charged_at_and_adds_refunded_at():
    """编译出来的 UPDATE 必须两件事都做：删旧键、加新键。只删不加就没留痕，
    只加不删就还顶着一个「本机制收过钱」的戳。"""
    from sqlalchemy.dialects.postgresql import dialect

    sql = str(mod._refunded_stamp_stmt(800000000000001, "2026-09-17T08:00:00+00:00"))
    compiled = mod._refunded_stamp_stmt(
        800000000000001, "2026-09-17T08:00:00+00:00"
    ).compile(dialect=dialect())
    assert "metadata_json" in sql
    bound = {v for v in compiled.params.values() if isinstance(v, str)}
    assert {"billing", "charged_at", "refunded_at"} <= bound, bound
