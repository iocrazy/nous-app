"""Phase 3 — token billing reconciliation + summary tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.billing import token_billing as tb

# ─── summarize_user_usage ────────────────────────────────────────────


def _row(model="qwen-max", tokens=100, cost=1.5, days_ago=0):
    # The ORM read yields a native datetime; summarize serializes it to ISO.
    return {
        "model": model,
        "total_tokens": tokens,
        "cost_points": cost,
        "created_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


def _read_scope_returning(rows):
    """read_scope() stand-in whose session.execute().mappings().all()
    returns ``rows`` (the ai_usage_logs row mappings)."""

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _Session:
        async def execute(self, *_a, **_kw):
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


def _read_scope_raising():
    """read_scope() stand-in whose session.execute() raises — the DB-down path."""

    class _Session:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("supabase down")

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


def _write_scope(ok: bool = True):
    """write_scope() stand-in; execute() succeeds or raises per ``ok``."""

    class _Session:
        async def execute(self, *_a, **_kw):
            if not ok:
                raise RuntimeError("no table")
            return MagicMock()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_aggregates_by_model_and_day():
    rows = [
        _row("qwen-max", tokens=100, cost=1.0, days_ago=0),
        _row("qwen-max", tokens=200, cost=2.0, days_ago=0),
        _row("claude-sonnet", tokens=300, cost=5.0, days_ago=1),
    ]
    with patch("app.db.session.read_scope", new=_read_scope_returning(rows)):
        s = await tb.summarize_user_usage(uuid4(), days=7)

    assert s.overall_total_tokens == 600
    assert s.overall_cost_points == 8.0
    assert s.overall_run_count == 3
    # by_model sorted by cost desc — claude (5) before qwen (3)
    assert s.by_model[0].model == "claude-sonnet"
    assert s.by_model[0].cost_points == 5.0
    assert s.by_model[1].model == "qwen-max"
    assert s.by_model[1].cost_points == 3.0
    assert s.by_model[1].run_count == 2
    # by_day sorted ascending (chronological)
    assert len(s.by_day) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_handles_empty_rows():
    with patch("app.db.session.read_scope", new=_read_scope_returning([])):
        s = await tb.summarize_user_usage(uuid4(), days=7)
    assert s.overall_total_tokens == 0
    assert s.overall_cost_points == 0.0
    assert s.by_model == []
    assert s.by_day == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_swallows_db_error():
    """DB failure → empty summary, not crash."""
    with patch("app.db.session.read_scope", new=_read_scope_raising()):
        s = await tb.summarize_user_usage(uuid4(), days=7)
    assert s.overall_run_count == 0


# ─── reconcile_run ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_byo_key_skips_points():
    """BYO-key runs log usage but never charge points."""
    with patch("app.db.session.write_scope", new=_write_scope(ok=True)):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="custom-byo-model",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=5.0,
            byo_key=True,
        )
    assert result.byo_key is True
    assert result.charged is False
    assert result.charged_points == 0.0
    assert result.usage_logged is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_no_team_skips_points():
    """Personal-scope run (no team_id) just logs."""
    with patch("app.db.session.write_scope", new=_write_scope(ok=True)):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=None,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=5.0,
            byo_key=False,
        )
    assert result.charged is False
    assert "no team_id" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_zero_cost_skips_points():
    """Free run (cost_points=0) doesn't try to charge."""
    with patch("app.db.session.write_scope", new=_write_scope(ok=True)):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=0.0,
            byo_key=False,
        )
    assert result.charged is False
    assert "zero cost" in (result.note or "")


# ``check_and_consume`` 的真签名（points_service.py）。集合断言而非逐个 in：
# 此前这里传的是 points= / action= / metadata= 三个**不存在**的关键字，外加缺了
# 必填的 user_id / action_type —— 每一次 completed + cost>0 的 run 都 TypeError，
# 被 reconcile_run 的 except 吞成 WARNING，积分一分没扣。精确匹配才能挡住再漂。
_REAL_CONSUME_KWARGS = {
    "team_id",
    "user_id",
    "action_type",
    "reference_id",
    "override_cost",
    "description",
}


def _fake_points(result):
    ps = MagicMock()
    ps.check_and_consume = AsyncMock(return_value=result)
    ps.ensure_team_quota = AsyncMock(return_value={"team_id": "42"})
    return ps


async def _reconcile(ps, **over):
    kwargs = dict(
        run_id="900000000000007",
        user_id=uuid4(),
        team_id=42,
        project_id=None,
        session_id=None,
        agent_id=uuid4(),
        model="nous_qwen-max",
        prompt_tokens=10,
        completion_tokens=20,
        cost_points=2.5,
        byo_key=False,
    )
    kwargs.update(over)
    with (
        patch("app.db.session.write_scope", new=_write_scope(ok=True)),
        patch("app.services.billing.points_service.PointsService", return_value=ps),
    ):
        return await tb.reconcile_run(**kwargs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_calls_the_real_signature_exactly():
    """四个关键字全不匹配、两个必填缺失，每轮 TypeError 被吞成 WARNING ——
    参数集合**精确**匹配，多一个少一个都红，防再次静默漂移。"""
    ps = _fake_points(
        {"success": True, "points_cost": 3, "balance_after": 97, "reason": None}
    )
    result = await _reconcile(ps)
    ps.check_and_consume.assert_awaited_once()
    _, kwargs = ps.check_and_consume.call_args
    assert set(kwargs) == _REAL_CONSUME_KWARGS
    assert kwargs["team_id"] == "42" and kwargs["action_type"] == "agent_run"
    assert kwargs["reference_id"] == "900000000000007"
    assert kwargs["override_cost"] == 3  # ceil(2.5)
    assert "nous_qwen-max" in kwargs["description"]
    assert "30 tokens" in kwargs["description"]
    assert result.charged is True and result.charged_points == 2.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_charge_lands_as_the_row_shape_the_readers_query():
    """效率账（Task 9/10）按 reference_type='agent_run' + reference_id=<run_id>
    读 point_transactions，`charged_points = -SUM(amount)`。PointsService 把
    ``reference_type`` 原样写成 ``action_type``、``amount`` 写成负数，所以这两个
    关键字就是那张表的形状契约 —— 换成 self.trigger 之类的动态值，读方立刻查空。"""
    ps = _fake_points(
        {"success": True, "points_cost": 3, "balance_after": 97, "reason": None}
    )
    await _reconcile(ps, action="chat")
    kwargs = ps.check_and_consume.call_args[1]
    # reference_type ← action_type：必须是字面量 agent_run，不跟 trigger 走。
    assert kwargs["action_type"] == "agent_run"
    assert kwargs["reference_id"] == "900000000000007"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_team_gets_provisioned_before_the_debit():
    """team_quotas 行不是注册时建的 —— mig 239 明确把它留给
    PointsService.ensure_team_quota，而八个 router 调用方每一个都先调它。
    这条路径不调，就意味着「第一次付费动作恰好是 agent run」的团队永远拿
    `Team quota not found`（rpc_consume_team_points, mig 120）而扣不到分 ——
    等于本 Task 要修的那个缺陷换了个机制继续存在。"""
    ps = _fake_points(
        {"success": True, "points_cost": 3, "balance_after": 97, "reason": None}
    )
    await _reconcile(ps)
    ps.ensure_team_quota.assert_awaited_once()
    # 顺序要紧：先建配额行再扣，反过来第一次必然扣空。
    assert ps.mock_calls.index(
        next(c for c in ps.mock_calls if c[0] == "ensure_team_quota")
    ) < ps.mock_calls.index(
        next(c for c in ps.mock_calls if c[0] == "check_and_consume")
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_kill_switch_provisions_nothing_either():
    """急停时不该顺手给团队开配额 + 发欢迎积分 —— 关掉的是整条计费路径。"""
    ps = _fake_points(
        {"success": True, "points_cost": 3, "balance_after": 97, "reason": None}
    )
    with patch.object(tb.settings, "AGENT_POINTS_CHARGE_ENABLED", False):
        await _reconcile(ps)
    ps.ensure_team_quota.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_empty_dict_with_success_false_is_not_a_charge():
    """旧代码 `charged=bool(ok)` 对非空 dict 恒 True —— 余额不足被记成扣过。"""
    ps = _fake_points(
        {
            "success": False,
            "points_cost": 3,
            "balance_after": 0,
            "reason": "Insufficient balance",
        }
    )
    result = await _reconcile(ps)
    assert result.charged is False and result.charged_points == 0.0
    assert "Insufficient balance" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_rpc_unavailable_shape_is_also_not_a_charge():
    """RPC 不可用时返回 dict（不 raise）—— 必须与「扣成功」分开，否则一次服务
    降级会被记成一次收费。"""
    ps = _fake_points(
        {
            "success": False,
            "points_cost": 3,
            "balance_after": None,
            "reason": "Points service temporarily unavailable.",
        }
    )
    result = await _reconcile(ps)
    assert result.charged is False
    assert "temporarily unavailable" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_kill_switch_skips_the_charge_but_still_logs_usage():
    ps = _fake_points(
        {"success": True, "points_cost": 3, "balance_after": 97, "reason": None}
    )
    with patch.object(tb.settings, "AGENT_POINTS_CHARGE_ENABLED", False):
        result = await _reconcile(ps)
    ps.check_and_consume.assert_not_awaited()
    assert result.charged is False and result.usage_logged is True
    assert result.note == "charging disabled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fractional_cost_rounds_up_never_to_zero():
    """0.3 分的 run 扣 1 分 —— 向下取整会让一整类小额 run 白跑。"""
    ps = _fake_points(
        {"success": True, "points_cost": 1, "balance_after": 99, "reason": None}
    )
    await _reconcile(ps, cost_points=0.3)
    assert ps.check_and_consume.call_args[1]["override_cost"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_points_failure_does_not_raise():
    """PointsService raising → ReconcileResult with charged=False, not exception."""
    fake_ps = MagicMock()
    fake_ps.check_and_consume = AsyncMock(
        side_effect=RuntimeError("insufficient balance")
    )

    with (
        patch("app.db.session.write_scope", new=_write_scope(ok=True)),
        patch(
            "app.services.billing.points_service.PointsService", return_value=fake_ps
        ),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=2.0,
            byo_key=False,
        )
    assert result.charged is False
    assert result.usage_logged is True  # log still wrote
    assert "errored" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_log_failure_still_returns():
    """ai_usage_logs insert failing → usage_logged=False, but caller still
    gets a ReconcileResult (no exception)."""
    with patch("app.db.session.write_scope", new=_write_scope(ok=False)):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=None,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=1,
            completion_tokens=1,
            cost_points=0.0,
            byo_key=True,
        )
    assert result.usage_logged is False
    assert result.byo_key is True
