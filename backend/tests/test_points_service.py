"""Unit tests for PointsService — covers the atomic consume / idempotent
refund paths introduced during the security hardening pass.

These tests pin the *contract* between PointsService and PointsRepository.
The RPCs themselves (migrations 120 and 123) are tested at the SQL level
and can't be exercised without a live Postgres; here we mock the repo.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.billing.points_service import PointsService


class _FakeRepo:
    """Minimal test double for PointsRepository with recording AsyncMocks."""

    def __init__(self) -> None:
        self.get_pricing = AsyncMock(
            return_value={"points_cost": 10, "is_active": True}
        )
        self.consume_points_atomic = AsyncMock()
        self.refund_points_atomic = AsyncMock()
        self.create_transaction = AsyncMock(return_value={})
        self.get_team_quota = AsyncMock(return_value={"points_balance": 100})
        self.create_team_quota = AsyncMock(return_value={"points_balance": 0})
        self.update_points_balance = AsyncMock(return_value={})
        self.increment_points_balance = AsyncMock(return_value={"points_balance": 130})
        # (row, created) —— created 是欢迎积分该不该发的唯一依据。
        self.create_team_quota_if_absent = AsyncMock(
            return_value=({"points_balance": 500}, True)
        )


@pytest.fixture
def service() -> PointsService:
    svc = PointsService()
    svc.repo = _FakeRepo()  # type: ignore[assignment]
    return svc


# ---------------------------------------------------------------------------
# check_and_consume — atomic consume flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consume_happy_path_uses_rpc(service: PointsService) -> None:
    """Successful consume should delegate to the atomic RPC, not read-then-write."""
    service.repo.consume_points_atomic.return_value = {  # type: ignore[attr-defined]
        "success": True,
        "points_cost": 10,
        "balance_after": 90,
        "reason": None,
    }

    result = await service.check_and_consume(
        team_id="team-1",
        user_id="user-1",
        action_type="ai_transcription",
    )

    assert result["success"] is True
    assert result["points_cost"] == 10
    assert result["balance_after"] == 90
    service.repo.consume_points_atomic.assert_awaited_once()  # type: ignore[attr-defined]
    service.repo.create_transaction.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_consume_insufficient_balance_does_not_record_tx(
    service: PointsService,
) -> None:
    """On denial, no transaction row should be written."""
    service.repo.consume_points_atomic.return_value = {  # type: ignore[attr-defined]
        "success": False,
        "points_cost": 10,
        "balance_after": 3,
        "reason": "Insufficient points balance. Required: 10, available: 3.",
    }

    result = await service.check_and_consume(
        team_id="team-1",
        user_id="user-1",
        action_type="ai_transcription",
    )

    assert result["success"] is False
    assert "Insufficient" in result["reason"]
    service.repo.create_transaction.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_consume_rpc_unavailable_fails_safely(
    service: PointsService,
) -> None:
    """If the RPC itself fails, we must not fall back to the old unsafe flow."""
    service.repo.consume_points_atomic.return_value = None  # type: ignore[attr-defined]

    result = await service.check_and_consume(
        team_id="team-1",
        user_id="user-1",
        action_type="ai_transcription",
    )

    assert result["success"] is False
    assert "unavailable" in result["reason"].lower()
    service.repo.create_transaction.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_consume_zero_cost_short_circuits(service: PointsService) -> None:
    service.repo.get_pricing.return_value = {  # type: ignore[attr-defined]
        "points_cost": 0,
        "is_active": True,
    }

    result = await service.check_and_consume(
        team_id="team-1",
        user_id="user-1",
        action_type="free_action",
    )

    assert result["success"] is True
    assert result["points_cost"] == 0
    service.repo.consume_points_atomic.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# refund_points — idempotent refund flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_happy_path(service: PointsService) -> None:
    service.repo.refund_points_atomic.return_value = {  # type: ignore[attr-defined]
        "success": True,
        "already_refunded": False,
        "new_balance": 50,
    }

    result = await service.refund_points(
        team_id="team-1",
        user_id="user-1",
        amount=10,
        reference_type="ai_transcription",
        reference_id="task-abc",
        reason="timeout",
    )

    assert result["success"] is True
    assert result["already_refunded"] is False
    assert result["new_balance"] == 50


@pytest.mark.asyncio
async def test_refund_already_refunded_is_noop(service: PointsService) -> None:
    """Second refund for the same reference returns already_refunded=True."""
    service.repo.refund_points_atomic.return_value = {  # type: ignore[attr-defined]
        "success": True,
        "already_refunded": True,
        "new_balance": 50,
    }

    result = await service.refund_points(
        team_id="team-1",
        user_id="user-1",
        amount=10,
        reference_type="ai_transcription",
        reference_id="task-abc",
        reason="timeout",
    )

    assert result["success"] is True
    assert result["already_refunded"] is True


@pytest.mark.asyncio
async def test_refund_rpc_unavailable(service: PointsService) -> None:
    service.repo.refund_points_atomic.return_value = None  # type: ignore[attr-defined]

    result = await service.refund_points(
        team_id="team-1",
        user_id="user-1",
        amount=10,
        reference_type="ai_transcription",
        reference_id="task-abc",
        reason="timeout",
    )

    assert result["success"] is False
    assert result["already_refunded"] is False


@pytest.mark.asyncio
async def test_refund_passes_reference_to_repo(service: PointsService) -> None:
    """The RPC needs the reference so the unique index can dedup retries."""
    service.repo.refund_points_atomic.return_value = {  # type: ignore[attr-defined]
        "success": True,
        "already_refunded": False,
        "new_balance": 50,
    }

    await service.refund_points(
        team_id="team-1",
        user_id="user-1",
        amount=10,
        reference_type="ai_transcription",
        reference_id="task-xyz",
        reason="retry",
    )

    call: Any = service.repo.refund_points_atomic.await_args  # type: ignore[attr-defined]
    assert call.kwargs["reference_type"] == "ai_transcription"
    assert call.kwargs["reference_id"] == "task-xyz"
    assert call.kwargs["amount"] == 10


# ---------------------------------------------------------------------------
# 落库形状 —— Task 9/10 的效率账直接按这三个字段反查（3c A3 评审轮 1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_consume_row_keeps_action_type_as_reference_type(
    service: PointsService,
) -> None:
    """``action_type`` 原样落进 ``point_transactions.reference_type``，``amount``
    为负，``type`` 是 'consume'。

    这一跳此前没有任何测试钉住：token_billing 侧的 row-shape 用例断的是**入参**
    （kwargs["action_type"] == "agent_run"），所以有人在这里给 reference_type
    加个前缀、或改成别的列，那边照样全绿，而读方 `WHERE reference_type =
    'agent_run'` 立刻查空 —— 积分扣了，效率账上一分钱都看不见。"""
    service.repo.consume_points_atomic = AsyncMock(  # type: ignore[assignment]
        return_value={"success": True, "balance_after": 97, "reason": None}
    )

    out = await service.check_and_consume(
        team_id="42",
        user_id="u-1",
        action_type="agent_run",
        reference_id="900000000000007",
        override_cost=3,
        description="nous_qwen-max · 30 tokens",
    )

    assert out["success"] is True
    payload = service.repo.create_transaction.await_args.args[0]
    assert payload["type"] == "consume"
    assert payload["reference_type"] == "agent_run"  # == action_type，原样
    assert payload["reference_id"] == "900000000000007"
    assert payload["amount"] == -3  # 负数：读方按 -SUM(amount) 还原扣了多少
    assert payload["team_id"] == "42"


@pytest.mark.asyncio
async def test_a_denied_consume_writes_no_transaction(service: PointsService) -> None:
    """余额不足不该在流水里留下一行 —— 否则 -SUM(amount) 会把没扣成的也算进去。"""
    service.repo.consume_points_atomic = AsyncMock(  # type: ignore[assignment]
        return_value={
            "success": False,
            "balance_after": 0,
            "reason": "Insufficient balance",
        }
    )

    out = await service.check_and_consume(
        team_id="42", user_id="u-1", action_type="agent_run", override_cost=3
    )

    assert out["success"] is False
    service.repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_losing_the_provisioning_race_grants_no_second_welcome_bonus(
    service: PointsService,
) -> None:
    """并发首次开通时两边都发 500 分，就是凭空多给一个团队一份赠送。
    ``created=False`` 的那一方只拿行，不写 gift 流水。"""
    service.repo.get_team_quota = AsyncMock(return_value=None)  # type: ignore[assignment]
    service.repo.create_team_quota_if_absent = AsyncMock(  # type: ignore[assignment]
        return_value=({"points_balance": 500}, False)
    )

    await service.ensure_team_quota("42", user_id="u-1")

    service.repo.create_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_winning_the_provisioning_race_does_grant_the_bonus(
    service: PointsService,
) -> None:
    service.repo.get_team_quota = AsyncMock(return_value=None)  # type: ignore[assignment]

    await service.ensure_team_quota("42", user_id="u-1")

    payload = service.repo.create_transaction.await_args.args[0]
    assert payload["type"] == "gift"
    assert payload["reference_type"] == "welcome_bonus"
    assert payload["amount"] == 500


# ---------------------------------------------------------------------------
# add_points — 充值必须走服务端自增
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_points_uses_increment_not_read_modify_write(
    service: PointsService,
) -> None:
    """读-算-写会在两次并发充值时丢一次更新 —— 真金白银（admin 赠送 / 调整都走
    这条路）。余额由服务端自增给出，不由 Python 算。"""
    out = await service.add_points(
        team_id="7", amount=30, type="gift", description="test", user_id="u-1"
    )

    service.repo.update_points_balance.assert_not_awaited()  # type: ignore[attr-defined]
    assert service.repo.increment_points_balance.await_args.args == ("7", 30)  # type: ignore[attr-defined]
    # balance_after 取自自增返回的行，不是 read 到的 100 + 30。
    payload = service.repo.create_transaction.await_args.args[0]
    assert payload["balance_after"] == 130
    assert out == {"success": True, "new_balance": 130}


@pytest.mark.asyncio
async def test_add_points_raises_when_the_increment_matched_no_quota_row(
    service: PointsService,
) -> None:
    """自增没命中任何行 = 这笔钱没加上。绝不能接着写一条余额是瞎编的流水。"""
    service.repo.increment_points_balance = AsyncMock(return_value={})  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        await service.add_points(
            team_id="7", amount=30, type="gift", description="test", user_id="u-1"
        )

    service.repo.create_transaction.assert_not_awaited()
