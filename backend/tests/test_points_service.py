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

from app.services.points_service import PointsService


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
