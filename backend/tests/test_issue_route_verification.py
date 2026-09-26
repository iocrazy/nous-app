"""Only a verified pass may auto-close (spec §5.4, deviation 4)."""

from unittest.mock import AsyncMock

import pytest

from app.workflows.issue_lifecycle import (
    _run_dispatch_with_continuation,
    route_finish_outcome,
)

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests


async def _route(auto_close, verification):
    set_status = AsyncMock()
    await route_finish_outcome(
        1,
        "completed",
        "r",
        auto_close=auto_close,
        set_status=set_status,
        content_len=5,
        disarm_wakeups=AsyncMock(),
        verification=verification,
    )
    return set_status.await_args.args[1]


@pytest.mark.parametrize(
    "auto_close,verification,expected",
    [
        (True, {"verdict": "pass"}, "done"),
        (True, {"verdict": "fail"}, "in_review"),
        (True, {"verdict": "unverified"}, "in_review"),
        (True, None, "in_review"),
        (False, {"verdict": "pass"}, "in_review"),
    ],
)
async def test_completed_routing_by_verdict(auto_close, verification, expected):
    assert await _route(auto_close, verification) == expected


def _deps(result):
    calls = []

    async def run_turn(issue_row, agent_id, user_id, *, is_continuation):
        return result

    async def set_status(issue_id, status, **kw):
        calls.append(status)

    async def load(issue_id):
        return {"id": issue_id, "status": "in_progress"}

    async def read_status(issue_id):
        return "in_progress"

    return calls, dict(
        run_turn=run_turn,
        set_status=set_status,
        load_issue=load,
        read_status=read_status,
    )


async def test_dispatch_loop_threads_verification_into_routing():
    calls, deps = _deps(
        {
            "content": "x",
            "outcome": "completed",
            "reason": "r",
            "verification": {"verdict": "pass"},
        }
    )
    await _run_dispatch_with_continuation(
        1, {"id": 1}, "a", "u", auto_close=True, **deps
    )
    assert calls[-1] == "done"


async def test_dispatch_loop_without_verdict_does_not_close():
    calls, deps = _deps({"content": "x", "outcome": "completed", "reason": "r"})
    await _run_dispatch_with_continuation(
        1, {"id": 1}, "a", "u", auto_close=True, **deps
    )
    assert calls[-1] == "in_review"
