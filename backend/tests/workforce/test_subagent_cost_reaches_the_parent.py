"""What a background sub-agent cost has to reach the parent's cost tree.

Task 7b defect A. ``_build_envelope`` returned ``tokens_used`` but no
``cost_cents``, and the worker reads ``envelope.get("cost_cents") or 0`` — so
every background child reported exactly zero. The 2026-09-10 re-verification
measured the gap on one run: the child's ``agent_runs.cost_cents`` was
0.0236 while the parent's ``cost.by_child`` said ``{"…286155642": 0.0}``, and
``subagent_done`` and the inbox row both said 0 too.

The token count was RIGHT (1725), which is what made it hard to see: the
symptom is "the numbers are there, one of them is zero", not a blank panel.
The synchronous path was never affected — it reads the recorder directly
(``_cost_cents_of``); only the envelope the background path travels in dropped
the field.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import (
    ENVELOPE_KEYS,
    SubAgentTaskService,
)

pytestmark = pytest.mark.unit


class _Recorder:
    """A child recorder that spent real money: token counters plus the folded
    ``cost`` view a run accumulates from its ``step_end`` events."""

    def __init__(self, *, own=0.0236, by_child=None, rates=True):
        self.prompt_tokens = 1500
        self.completion_tokens = 225
        self._own = own
        self.views = {
            "cost": {
                "own_cents": own,
                "by_child": by_child or {},
                "spent_cents": round(own + sum((by_child or {}).values()), 4),
            }
        }
        self._rates = rates

    def compute_cost_cents(self) -> float:
        return self._own if self._rates else 0.0


# ── the envelope ───────────────────────────────────────────────────────


def test_the_envelope_carries_what_the_child_spent():
    env = SubAgentTaskService._build_envelope(
        result={"content": "OK"}, sub_run_id=52, recorder=_Recorder()
    )
    assert env["cost_cents"] == pytest.approx(0.0236)
    assert env["tokens_used"] == 1725
    assert "cost_cents" in ENVELOPE_KEYS


def test_the_envelope_bills_the_child_s_own_children_too():
    """``by_child`` on the parent is one number per child, and that number is
    the child's TOTAL. A child that fanned out synchronously spent its
    grandchildren's cents as surely as its own."""
    env = SubAgentTaskService._build_envelope(
        result={"content": "OK"},
        sub_run_id=52,
        recorder=_Recorder(own=0.02, by_child={"77": 0.5}),
    )
    assert env["cost_cents"] == pytest.approx(0.52)


def test_a_model_with_no_price_row_reports_zero_rather_than_raising():
    """Rates are unknown for some models; the UI shows '—' for that. It is
    still a number here, and telemetry never fails a turn."""
    env = SubAgentTaskService._build_envelope(
        result={"content": "OK"}, sub_run_id=52, recorder=None
    )
    assert env["cost_cents"] == 0.0


def test_the_never_launched_failure_envelope_has_the_key_too():
    out = SubAgentTaskService._failed("unknown agent slug")
    assert out["cost_cents"] == 0.0
    for key in ENVELOPE_KEYS:
        assert key in out


# ── the worker forwards it ─────────────────────────────────────────────


def _task():
    payload = {
        "kind": "subagent",
        "parent_run_id": "900",
        "caller_agent_id": str(uuid4()),
        "subagent_type": "summarize",
        "prompt": "dig",
        "description": "probe",
        "child_run_id": None,
        "reply_to": {"target_kind": "issue", "target_id": 7},
        "user_id": str(uuid4()),
        "agent_depth": 0,
    }
    task_id = str(uuid4())
    return {
        "id": task_id,
        "agent_id": str(uuid4()),
        "user_id": payload["user_id"],
        "lifecycle_status": "assigned",
        "payload": payload,
        "workforce_workflow_id": f"workforce-{task_id}-1",
    }


async def test_the_worker_puts_the_cost_on_the_inbox_row_and_on_subagent_done():
    """Both surfaces, because they feed different readers: the inbox row is
    what the parent's next turn reads, and ``subagent_done`` is what
    ``cost.by_child`` folds."""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    from app.services.ai.runner import run_recorder as recorder_mod
    from app.services.workforce.agent_worker import run_one_task

    task = _task()
    workforce = MagicMock()
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": "ob-1"})
    workforce.claim_task = AsyncMock(return_value=task)
    inbox = SimpleNamespace(enqueue=AsyncMock(return_value={"id": 1}))
    writer = SimpleNamespace(append=AsyncMock())

    stack = [
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=MagicMock(),
        ),
        patch.object(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox),
        patch.object(
            recorder_mod.RunEventWriter, "for_run", AsyncMock(return_value=writer)
        ),
        patch(
            "app.services.ai.runner.subagent_task_service.SubAgentTaskService."
            "run_background_task",
            AsyncMock(
                return_value={
                    "status": "success",
                    "summary": "OK",
                    "sub_run_id": "52",
                    "tokens_used": 1725,
                    "cost_cents": 0.0236,
                }
            ),
        ),
    ]
    for p in stack:
        p.start()
    try:
        await run_one_task(task)
    finally:
        for p in reversed(stack):
            p.stop()

    content = inbox.enqueue.await_args.kwargs["content"]
    assert content["cost_cents"] == pytest.approx(0.0236)
    assert content["tokens_used"] == 1725

    event_type, payload = writer.append.await_args.args
    assert event_type == "subagent_done"
    assert payload["cost_cents"] == pytest.approx(0.0236)
    assert payload["tokens_used"] == 1725
