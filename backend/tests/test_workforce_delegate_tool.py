"""Unit tests for DelegateToolService.

Pins the cross-agent dispatch contract:
    - Validates required args (agent_slug + prompt)
    - Rejects self-delegate
    - Rejects depth > MAX_DELEGATION_DEPTH
    - Rejects non-persistent target
    - Writes both inbox + outbox rows on success
    - Carries parent_run_id + depth into the inbox payload
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.workforce.delegate_tool import (
    MAX_DELEGATION_DEPTH,
    DelegateToolService,
)


def _build_repos(*, target: dict | None = None):
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=target)

    workforce = MagicMock()
    workforce.enqueue_inbox = AsyncMock(return_value={"id": str(uuid4())})
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})
    return agent_repo, workforce


def _service(*, depth: int = 0, target: dict | None = None) -> tuple:
    caller_aid = uuid4()
    caller_uid = uuid4()
    parent_run = uuid4()
    agent_repo, workforce = _build_repos(target=target)
    svc = DelegateToolService(
        caller_agent_id=caller_aid,
        caller_user_id=caller_uid,
        parent_run_id=parent_run,
        agent_depth=depth,
        agent_repo=agent_repo,
        workforce_repo=workforce,
    )
    return svc, agent_repo, workforce, caller_aid, caller_uid, parent_run


# ─── arg validation ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_slug_returns_error():
    svc, *_ = _service()
    out = await svc.execute({"prompt": "hi"})
    assert "error" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_prompt_returns_error():
    svc, *_ = _service()
    out = await svc.execute({"agent_slug": "summary"})
    assert "error" in out


# ─── target lookup failures ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_target_slug_returns_error():
    svc, agent_repo, workforce, *_ = _service(target=None)
    out = await svc.execute({"agent_slug": "ghost", "prompt": "hi"})
    assert "error" in out
    workforce.enqueue_inbox.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_persistent_target_rejected():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": False}
    svc, _, workforce, *_ = _service(target=target)
    out = await svc.execute({"agent_slug": "summary", "prompt": "hi"})
    assert "error" in out
    assert "persistent" in out["error"]
    workforce.enqueue_inbox.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_self_delegate_rejected():
    """Caller agent_id == target agent_id → blocked."""
    caller_aid = uuid4()
    target = {"id": str(caller_aid), "slug": "self", "persistent": True}
    agent_repo, workforce = _build_repos(target=target)
    svc = DelegateToolService(
        caller_agent_id=caller_aid,
        caller_user_id=uuid4(),
        parent_run_id=None,
        agent_depth=0,
        agent_repo=agent_repo,
        workforce_repo=workforce,
    )
    out = await svc.execute({"agent_slug": "self", "prompt": "hi"})
    assert "error" in out
    assert "self" in out["error"]
    workforce.enqueue_inbox.assert_not_called()


# ─── depth limit ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_depth_at_or_above_max_rejected():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(depth=MAX_DELEGATION_DEPTH, target=target)
    out = await svc.execute({"agent_slug": "summary", "prompt": "hi"})
    assert "error" in out
    assert "depth" in out
    workforce.enqueue_inbox.assert_not_called()


# ─── happy path ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_writes_inbox_and_outbox():
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}
    svc, _, workforce, caller_aid, caller_uid, parent_run = _service(
        target=target
    )
    out = await svc.execute(
        {
            "agent_slug": "summary",
            "prompt": "Summarise this PR.",
            "title": "PR review",
            "priority": 7,
        }
    )

    assert "error" not in out
    assert out["delegated_to"] == "summary"
    assert out["agent_id"] == str(target_aid)
    assert out["status"] == "queued"
    assert out["depth"] == 1

    inbox_kwargs = workforce.enqueue_inbox.await_args.kwargs
    assert inbox_kwargs["recipient_agent_id"] == target_aid
    assert inbox_kwargs["sender_kind"] == "agent"
    assert inbox_kwargs["sender_agent_id"] == caller_aid
    assert inbox_kwargs["sender_user_id"] == caller_uid
    assert inbox_kwargs["message_type"] == "task"
    assert inbox_kwargs["priority"] == 7
    assert inbox_kwargs["payload"]["prompt"] == "Summarise this PR."
    assert inbox_kwargs["payload"]["delegated_by"] == str(caller_aid)
    assert inbox_kwargs["payload"]["parent_run_id"] == str(parent_run)
    assert inbox_kwargs["payload"]["delegated_at_depth"] == 0

    outbox_kwargs = workforce.enqueue_outbox.await_args.kwargs
    assert outbox_kwargs["sender_agent_id"] == caller_aid
    assert outbox_kwargs["recipient_kind"] == "agent"
    assert outbox_kwargs["recipient_agent_id"] == target_aid


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_failure_surfaces_as_error():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    workforce.enqueue_inbox = AsyncMock(return_value=None)  # simulate DB fail

    out = await svc.execute({"agent_slug": "summary", "prompt": "hi"})
    assert "error" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_key_forwarded():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    await svc.execute(
        {"agent_slug": "summary", "prompt": "hi", "dedup_key": "delegate-001"}
    )
    assert (
        workforce.enqueue_inbox.await_args.kwargs["dedup_key"] == "delegate-001"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_flag_forwarded_into_payload_and_response():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    out = await svc.execute(
        {"agent_slug": "summary", "prompt": "hi", "await": True}
    )
    assert out["await"] is True
    assert workforce.enqueue_inbox.await_args.kwargs["payload"]["await"] is True
