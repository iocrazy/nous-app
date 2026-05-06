"""G1 — approval requests router smoke tests."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.ai_library_router  # noqa: F401
router_module = sys.modules["app.api.ai_library_router"]

from app.repositories.approval_requests_repository import ApprovalRequest


def _row(user_id, status="pending"):
    now = datetime.now(timezone.utc)
    return ApprovalRequest(
        id=uuid4(),
        user_id=user_id,
        agent_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        hook_name="cost_auditor",
        reason="agent wants to spend $X",
        payload={"cost_cents": 100},
        status=status,
        decided_at=None,
        decided_by=None,
        decision_note=None,
        created_at=now,
        expires_at=now,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_returns_only_pending():
    user_id = uuid4()
    rows = [_row(user_id), _row(user_id)]
    fake_repo = MagicMock()
    fake_repo.list_pending_for_user = AsyncMock(return_value=rows)

    auth = MagicMock(); auth.user_id = str(user_id)
    with patch(
        "app.repositories.approval_requests_repository.ApprovalRequestsRepository",
        return_value=fake_repo,
    ):
        out = await router_module.list_approval_requests(auth)
    assert out["count"] == 2
    assert all(item["reason"] == "agent wants to spend $X" for item in out["items"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_404s_when_not_owner():
    owner = uuid4()
    intruder = uuid4()
    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=_row(owner))

    auth = MagicMock(); auth.user_id = str(intruder)
    payload = router_module._ApprovalDecision(note="x")
    with patch(
        "app.repositories.approval_requests_repository.ApprovalRequestsRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as e:
            await router_module.approve_approval_request(uuid4(), payload, auth)
        assert e.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_409s_when_already_decided():
    user_id = uuid4()
    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=_row(user_id, status="approved"))
    auth = MagicMock(); auth.user_id = str(user_id)
    payload = router_module._ApprovalDecision()
    with patch(
        "app.repositories.approval_requests_repository.ApprovalRequestsRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as e:
            await router_module.approve_approval_request(uuid4(), payload, auth)
        assert e.value.status_code == 409


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_happy_path_calls_decide_with_owner():
    user_id = uuid4()
    req_id = uuid4()
    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=_row(user_id))
    fake_repo.decide = AsyncMock(return_value=True)
    auth = MagicMock(); auth.user_id = str(user_id)
    payload = router_module._ApprovalDecision(note="lgtm")
    with patch(
        "app.repositories.approval_requests_repository.ApprovalRequestsRepository",
        return_value=fake_repo,
    ):
        out = await router_module.approve_approval_request(req_id, payload, auth)
    assert out["status"] == "approved"
    # Defensive: owner_user_id MUST be passed
    fake_repo.decide.assert_awaited_once()
    _, kwargs = fake_repo.decide.call_args
    assert kwargs["owner_user_id"] == user_id
    assert kwargs["approve"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reject_happy_path():
    user_id = uuid4()
    req_id = uuid4()
    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=_row(user_id))
    fake_repo.decide = AsyncMock(return_value=True)
    auth = MagicMock(); auth.user_id = str(user_id)
    payload = router_module._ApprovalDecision(note="too risky")
    with patch(
        "app.repositories.approval_requests_repository.ApprovalRequestsRepository",
        return_value=fake_repo,
    ):
        out = await router_module.reject_approval_request(req_id, payload, auth)
    assert out["status"] == "rejected"
    _, kwargs = fake_repo.decide.call_args
    assert kwargs["approve"] is False
    assert kwargs["note"] == "too risky"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lane_snapshot_returns_unavailable_when_not_wired():
    """Without lane_queue on app.state, snapshot says so cleanly."""
    auth = MagicMock(); auth.user_id = str(uuid4())
    # Simulate fresh app.state with no lane_queue
    fake_app = MagicMock()
    fake_app.state.lane_queue = None
    with patch("app.main.app", fake_app):
        out = await router_module.admin_lane_snapshot(auth)
    assert out["available"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lane_snapshot_reads_qsize_per_lane():
    """When lane_queue IS present, output is { lanes: { name: { depth } } }."""
    from enum import Enum

    class _Lane(Enum):
        USER = "user"
        BACKGROUND = "background"

    fake_q_user = MagicMock(); fake_q_user.qsize = MagicMock(return_value=3)
    fake_q_bg = MagicMock(); fake_q_bg.qsize = MagicMock(return_value=12)
    fake_lq = MagicMock()
    fake_lq._queues = {_Lane.USER: fake_q_user, _Lane.BACKGROUND: fake_q_bg}

    fake_app = MagicMock()
    fake_app.state.lane_queue = fake_lq

    auth = MagicMock(); auth.user_id = str(uuid4())
    with patch("app.main.app", fake_app):
        out = await router_module.admin_lane_snapshot(auth)
    assert out["available"] is True
    assert out["lanes"]["user"]["depth"] == 3
    assert out["lanes"]["background"]["depth"] == 12
