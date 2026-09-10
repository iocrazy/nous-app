"""Unit tests for DelegateToolService.

Pins the cross-agent dispatch contract:
    - Validates required args (agent_slug + prompt)
    - Rejects self-delegate
    - Rejects depth > MAX_DELEGATION_DEPTH
    - Rejects non-persistent target
    - Writes both inbox + outbox rows on success
    - Carries parent_run_id + depth into the inbox payload
    - Rate-limits per-caller (Q milestone)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.workforce import delegate_tool as _dt_mod
from app.services.workforce.delegate_tool import (
    MAX_DELEGATION_DEPTH,
    RATE_LIMIT_MAX_CALLS,
    DelegateToolService,
)


@pytest.fixture(autouse=True)
def _reset_rate_limit_history() -> None:
    """Each test starts with an empty rate-limit window so order doesn't
    leak between tests."""
    _dt_mod._dispatch_history.clear()
    yield
    _dt_mod._dispatch_history.clear()


@pytest.fixture(autouse=True)
def _enable_delegate(monkeypatch) -> None:
    """Audit #4: Delegate is gated off by default in prod
    (settings.FEATURE_WORKFORCE_DELEGATE). These tests pin the feature-ON
    contract; the default-off gate is covered by
    test_delegate_disabled_by_default."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_WORKFORCE_DELEGATE", True)


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
    svc, _, workforce, caller_aid, caller_uid, parent_run = _service(target=target)
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
    assert workforce.enqueue_inbox.await_args.kwargs["dedup_key"] == "delegate-001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_flag_forwarded_into_payload_and_response():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    # Stub cycle + lookup to terminate immediately; we're only pinning that
    # await=true is forwarded into the inbox payload and response shape.
    svc._detect_cycle = AsyncMock(return_value=None)
    svc._lookup_task_by_inbox = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "lifecycle_status": "done",
            "result": {"content": "ok"},
        }
    )
    out = await svc.execute(
        {
            "agent_slug": "summary",
            "prompt": "hi",
            "await": True,
            "await_timeout_seconds": 0.5,
        }
    )
    assert out["await"] is True
    assert workforce.enqueue_inbox.await_args.kwargs["payload"]["await"] is True


# ─── J milestone: cycle detection ───────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cycle_detected_rejects_dispatch():
    """If walking parent_run_id finds the target agent already on the
    chain, refuse the dispatch — that's a ping-pong cycle (A→B→A→B)
    that would slip past the depth-only check."""
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    ancestor_run = uuid4()
    svc._detect_cycle = AsyncMock(return_value=ancestor_run)

    out = await svc.execute({"agent_slug": "summary", "prompt": "loop"})
    assert "error" in out
    assert "cycle detected" in out["error"]
    assert out["cycle_run_id"] == str(ancestor_run)

    # No inbox/outbox written when cycle is detected.
    workforce.enqueue_inbox.assert_not_called()
    workforce.enqueue_outbox.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_cycle_proceeds_normally():
    """When the walker reports no cycle (None), dispatch proceeds."""
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)

    out = await svc.execute({"agent_slug": "summary", "prompt": "ok"})
    assert "error" not in out
    workforce.enqueue_inbox.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cycle_walker_returns_none_when_no_parent_run_id():
    """Without a parent_run_id (top-of-tree call), no walk is needed —
    the chain is empty, so no cycle is possible."""
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=target)
    workforce = MagicMock()
    workforce.enqueue_inbox = AsyncMock(return_value={"id": str(uuid4())})
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})
    svc = DelegateToolService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id=None,
        agent_depth=0,
        agent_repo=agent_repo,
        workforce_repo=workforce,
    )
    cycle = await svc._detect_cycle(target_agent_id=uuid4())
    assert cycle is None


# ─── F milestone: await=true ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_true_returns_result_when_task_done():
    """When await=true and target finishes within the timeout, the
    response carries the task's content + status='done'."""
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)

    task_id = uuid4()
    svc._lookup_task_by_inbox = AsyncMock(
        return_value={
            "id": str(task_id),
            "lifecycle_status": "done",
            "result": {"content": "the summary text", "run_id": str(uuid4())},
        }
    )

    out = await svc.execute(
        {"agent_slug": "summary", "prompt": "summarise it", "await": True}
    )
    assert out["status"] == "done"
    assert out["result"] == "the summary text"
    assert out["task_id"] == str(task_id)
    assert "waited_seconds" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_true_failed_task_returns_error_fields():
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)

    svc._lookup_task_by_inbox = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "lifecycle_status": "failed",
            "error_code": "runtime_error",
            "error_message": "model exploded",
        }
    )

    out = await svc.execute({"agent_slug": "summary", "prompt": "x", "await": True})
    assert out["status"] == "failed"
    assert out["error_code"] == "runtime_error"
    assert out["error_message"] == "model exploded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_true_timeout_when_task_never_terminal():
    """When the target never lands a terminal lifecycle within the
    timeout, status='timeout' and the inbox_message_id is preserved so
    the caller can follow up via status_query."""
    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)

    svc._lookup_task_by_inbox = AsyncMock(
        return_value={"id": str(uuid4()), "lifecycle_status": "in_progress"}
    )

    # Tight timeout so the test runs fast.
    out = await svc.execute(
        {
            "agent_slug": "summary",
            "prompt": "x",
            "await": True,
            "await_timeout_seconds": 0.5,
        }
    )
    assert out["status"] == "timeout"
    assert out["last_lifecycle"] == "in_progress"
    assert out["inbox_message_id"] is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_await_timeout_capped_at_max():
    """await_timeout_seconds is capped at MAX_AWAIT_TIMEOUT_S so a
    runaway prompt can't pin the agent forever."""
    from app.services.workforce.delegate_tool import (
        MAX_AWAIT_TIMEOUT_S,
    )

    target = {"id": str(uuid4()), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)
    # Resolve immediately so the timeout cap doesn't actually have to expire
    svc._lookup_task_by_inbox = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "lifecycle_status": "done",
            "result": {"content": "ok"},
        }
    )

    out = await svc.execute(
        {
            "agent_slug": "summary",
            "prompt": "x",
            "await": True,
            "await_timeout_seconds": 99999,  # absurd
        }
    )
    # We don't assert exact wait time; the cap is enforced inside execute.
    # Sanity-check: terminal path still returns done.
    assert out["status"] == "done"
    # And the cap exists.
    assert MAX_AWAIT_TIMEOUT_S < 99999


# ─── Q milestone: per-caller rate limit ────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_kicks_in_after_max_calls():
    """The 31st Delegate call from the same caller within the window is
    rejected with retry_after_seconds + the limit details."""
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}
    svc, _, workforce, *_ = _service(target=target)
    svc._detect_cycle = AsyncMock(return_value=None)

    # Fire RATE_LIMIT_MAX_CALLS legitimate dispatches — all should pass.
    for _ in range(RATE_LIMIT_MAX_CALLS):
        out = await svc.execute({"agent_slug": "summary", "prompt": "p"})
        assert "error" not in out, out

    # Next one trips the limit.
    rl = await svc.execute({"agent_slug": "summary", "prompt": "p"})
    assert "error" in rl
    assert "rate limit" in rl["error"]
    assert rl["limit"] == RATE_LIMIT_MAX_CALLS
    assert rl["window_seconds"] == 60
    assert rl["retry_after_seconds"] >= 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_is_per_caller_not_global():
    """Two different caller agents have independent windows. One getting
    rate-limited must not block the other."""
    target_aid = uuid4()
    target = {"id": str(target_aid), "slug": "summary", "persistent": True}

    # Caller A: pin to its limit.
    svc_a, _, _, *_ = _service(target=target)
    svc_a._detect_cycle = AsyncMock(return_value=None)
    for _ in range(RATE_LIMIT_MAX_CALLS):
        await svc_a.execute({"agent_slug": "summary", "prompt": "p"})
    capped = await svc_a.execute({"agent_slug": "summary", "prompt": "p"})
    assert "rate limit" in capped["error"]

    # Caller B: fresh window, first call should succeed.
    svc_b, _, _, *_ = _service(target=target)
    svc_b._detect_cycle = AsyncMock(return_value=None)
    out = await svc_b.execute({"agent_slug": "summary", "prompt": "p"})
    assert "error" not in out


# ─── audit #4: feature gate (default off) ────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegate_disabled_by_default(monkeypatch):
    """With FEATURE_WORKFORCE_DELEGATE unset, execute() fail-closes before
    touching repos — the inbox→worker chain isn't wired, so a queued
    delegation would orphan forever."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_WORKFORCE_DELEGATE", False)
    svc, agent_repo, workforce, *_ = _service(
        target={"id": str(uuid4()), "slug": "summary", "is_persistent": True}
    )

    out = await svc.execute({"agent_slug": "summary", "prompt": "hi"})

    assert "not enabled" in out["error"]
    agent_repo.get_by_slug.assert_not_awaited()
    workforce.enqueue_inbox.assert_not_awaited()
