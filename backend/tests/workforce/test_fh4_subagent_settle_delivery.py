"""fh4 T2 (E2): every background sub-agent result says WHY it settled, is
delivered once per task, and reaches the parent even when the worker died.

Four producers write one child's result — the worker's normal completion, the
worker's crash arm, a replayed step that finds the child already ran, and the
stale-task reaper closing a child whose worker is gone. Before fh4 only the
first two delivered to the parent's inbox, neither carried a dedupe key, and
none said whether the child finished, was stopped, or was lost.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runner.inbox import InboxItem, render_inbox_message
from app.services.ai.runner.inbox_hook import InboxClaimHook
from app.services.ai.runner.step_hooks import StepContext
from app.services.workforce import stale_tasks as st
from app.services.workforce.settle import SettleReason
from tests.workforce.test_run_one_task_subagent import _run, _task, _wire

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _inbox_kwargs(w):
    (call,) = w.inbox_repo.enqueue.await_args_list
    return call.kwargs


def _done_payload(w):
    event_type, payload = w.writer.append.await_args.args
    assert event_type == "subagent_done"
    return payload


# ── the worker's own paths ──────────────────────────────────────────────────


async def test_worker_result_is_deduped_per_task_and_says_producer():
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(return_value=None)
    task = _task()

    await _run(w, task)

    kw = _inbox_kwargs(w)
    assert kw["dedupe_key"] == f"subagent-result-{task['id']}"
    assert kw["content"]["settle_reason"] == "producer"
    assert kw["content"]["status"] == "success"
    assert _done_payload(w)["settle_reason"] == "producer"


async def test_a_cancelled_child_settles_as_kill_in_event_and_content():
    w = _wire(
        envelope={
            "status": "cancelled",
            "summary": "stopped",
            "sub_run_id": "52",
            "tokens_used": 1,
        }
    )
    w.workforce.latest_run_for_task = AsyncMock(return_value=None)

    await _run(w, _task())

    assert _inbox_kwargs(w)["content"]["settle_reason"] == "kill"
    assert _done_payload(w)["settle_reason"] == "kill"


async def test_a_crashed_childs_inbox_summary_is_the_error_text():
    """The model used to get an EMPTY frame for a crashed child: only the
    ``subagent_done`` event fell back to the error text."""
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(return_value=None)
    w.run_bg.side_effect = RuntimeError("provider exploded")

    await _run(w, _task())

    content = _inbox_kwargs(w)["content"]
    assert content["status"] == "failed"
    assert "provider exploded" in content["summary"]
    assert content["summary"] == _done_payload(w)["summary"]
    assert content["settle_reason"] == "producer"


async def test_a_replayed_step_delivers_the_finished_child_instead_of_rerunning():
    """DBOS replays ``run_one_task_step`` after a crash between the child's
    finish and the step checkpoint. The child must not run (and bill) twice."""
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(
        return_value={
            "id": "348000000000052",
            "status": "completed",
            "output_summary": "already answered",
            "error_code": None,
            "error_message": None,
            "cost_cents": 2.5,
            "total_tokens": 40,
        }
    )
    task = _task()

    out = await _run(w, task)

    w.run_bg.assert_not_awaited()
    kw = _inbox_kwargs(w)
    assert kw["dedupe_key"] == f"subagent-result-{task['id']}"
    assert kw["content"]["child_run_id"] == "348000000000052"
    assert kw["content"]["status"] == "success"
    assert kw["content"]["summary"] == "already answered"
    assert kw["content"]["cost_cents"] == 2.5
    assert out["status"] == "success" and out["run_id"] == "348000000000052"


async def test_a_replay_that_finds_a_running_row_still_runs_the_child():
    """Negative control: ``running`` is not a result to deliver."""
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(
        return_value={"id": "52", "status": "running"}
    )

    await _run(w, _task())

    w.run_bg.assert_awaited_once()


# ── the reaper ──────────────────────────────────────────────────────────────


class _Repo:
    def __init__(self, task, run):
        self.task = task
        self.run = run
        self.status_calls: list[dict] = []

    async def list_stale_active_tasks(self, *, older_than, limit):
        return [self.task]

    async def latest_run_for_task(self, task_id):
        return self.run

    async def update_task_status(self, **kwargs):
        self.status_calls.append(kwargs)
        return True

    async def requeue_task(self, task_id):
        raise AssertionError("not a requeue case")

    async def get_worker(self, agent_id):
        return None

    async def subagent_done_status(self, *, parent_run_id, task_id):
        return None


def _async_child_task():
    return {
        "id": "5bdca3e9-0000-4000-8000-000000000001",
        "agent_id": "5bdca3e9-0000-4000-8000-0000000000aa",
        "lifecycle_status": "assigned",
        "workforce_workflow_id": "workforce-x-1",
        "payload": {
            "kind": "subagent",
            "parent_run_id": "777",
            "subagent_type": "researcher",
            "description": "dig",
            "reply_to": {"target_kind": "issue", "target_id": 7},
            "user_id": "11111111-1111-4111-8111-111111111111",
        },
    }


@pytest.fixture
def reaper(monkeypatch):
    def _wire_reaper(run, *, stored=None):
        task = _async_child_task()
        repo = _Repo(task, run)
        emit = AsyncMock()

        async def _enqueue(**kw):
            # The dedupe arm hands back whatever is already live under the key.
            return stored or {"id": 1, "content": {**kw["content"]}}

        inbox = SimpleNamespace(enqueue=AsyncMock(side_effect=_enqueue))
        monkeypatch.setattr(st, "get_agent_workforce_repository", lambda: repo)
        monkeypatch.setattr(st, "_dbos_still_owns", AsyncMock(return_value=False))
        monkeypatch.setattr(st, "emit_async_subagent_done", emit)
        monkeypatch.setattr(st, "_move_worker_back_to_idle", AsyncMock())
        monkeypatch.setattr(st, "_child_row_cost_cents", AsyncMock(return_value=1.5))
        monkeypatch.setattr(st, "get_agent_run_inbox_repository", lambda: inbox)
        return SimpleNamespace(task=task, repo=repo, emit=emit, inbox=inbox)

    return _wire_reaper


async def test_worker_lost_delivers_a_failed_result_and_returns_a_wake_order(reaper):
    r = reaper({"id": "9001", "status": "heartbeat_lost", "error_code": None})

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    kw = r.inbox.enqueue.await_args.kwargs
    assert kw["kind"] == "subagent_result"
    assert (kw["target_kind"], kw["target_id"]) == ("issue", 7)
    assert kw["dedupe_key"] == f"subagent-result-{r.task['id']}"
    assert kw["content"]["status"] == "failed"
    assert kw["content"]["settle_reason"] == "lost"
    assert kw["content"]["child_run_id"] == "9001"
    assert "worker" in kw["content"]["summary"]
    assert r.emit.await_args.kwargs["settle_reason"] == SettleReason.LOST
    assert out["wake_orders"] == [
        {
            "task_id": r.task["id"],
            "idle_dispatch": {
                "issue_id": 7,
                "user_id": "11111111-1111-4111-8111-111111111111",
            },
        }
    ]


async def test_worker_shutdown_settles_as_teardown(reaper):
    r = reaper(
        {"id": "9001", "status": "heartbeat_lost", "error_code": "worker_shutdown"}
    )

    await st.reap_stale_workforce_tasks()

    assert r.inbox.enqueue.await_args.kwargs["content"]["settle_reason"] == "teardown"
    assert r.emit.await_args.kwargs["settle_reason"] == SettleReason.TEARDOWN


async def test_reaper_converges_on_a_result_the_worker_already_delivered(reaper):
    """The worker filed ``success`` on the inbox and died before its
    ``subagent_done``. The reaper must not tell the card ``failed`` while the
    model was told ``success``: the first writer wins, in both places."""
    stored = {
        "id": 55,
        "content": {
            "child_run_id": "9001",
            "status": "success",
            "settle_reason": "producer",
            "summary": "done it",
        },
    }
    r = reaper({"id": "9001", "status": "completed", "error_code": None}, stored=stored)

    out = await st.reap_stale_workforce_tasks()

    assert r.inbox.enqueue.await_count == 1
    kw = r.emit.await_args.kwargs
    assert kw["status"] == "success"
    assert kw["settle_reason"] == SettleReason.PRODUCER
    (call,) = r.repo.status_calls
    assert call["lifecycle_status"] == "done"
    assert out["done"] == 1 and out["failed"] == 0


async def test_a_delegate_task_gets_no_inbox_delivery(reaper, monkeypatch):
    """Delegate results go to the sender agent's workforce inbox by design;
    only the two Task paths deliver into the parent run's inbox."""
    r = reaper({"id": "5", "status": "failed", "error_code": None})
    r.task["payload"] = {}

    out = await st.reap_stale_workforce_tasks()

    r.inbox.enqueue.assert_not_awaited()
    assert out["wake_orders"] == []


# ── the consumer side (CLAUDE.md: every producer through the real consumer) ──


class _Rec:
    run_id = 42
    issue_id = 7
    conversation_id = None

    def __init__(self):
        self.events: list = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))


async def _through_claim_hook(content: dict) -> str:
    item = InboxItem.from_row(
        {
            "id": 310819108761499,
            "target_kind": "issue",
            "target_id": 7,
            "kind": "subagent_result",
            "content": content,
            "created_at": None,
            "user_id": "u",
        }
    )

    async def claim(targets, run_id, turn, step):
        return [item]

    async def resolve(*, issue_id=None, conversation_id=None):
        return [("issue", 7)]

    ctx = StepContext(turn=1, step=1, recorder=_Rec(), parent_run_id=None)
    await InboxClaimHook(claim=claim, resolve=resolve).before_llm_call(ctx)
    (msg,) = ctx.injected
    return msg["content"]


async def test_every_producer_reaches_the_model_with_status_and_reason(reaper):
    ok = _wire()
    ok.workforce.latest_run_for_task = AsyncMock(return_value=None)
    await _run(ok, _task())

    crashed = _wire()
    crashed.workforce.latest_run_for_task = AsyncMock(return_value=None)
    crashed.run_bg.side_effect = RuntimeError("provider exploded")
    await _run(crashed, _task())

    lost = reaper({"id": "9001", "status": "heartbeat_lost", "error_code": None})
    await st.reap_stale_workforce_tasks()

    cases = [
        (_inbox_kwargs(ok)["content"], 'status="success"', 'reason="producer"', "s"),
        (
            _inbox_kwargs(crashed)["content"],
            'status="failed"',
            'reason="producer"',
            "provider exploded",
        ),
        (
            lost.inbox.enqueue.await_args.kwargs["content"],
            'status="failed"',
            'reason="lost"',
            "worker",
        ),
    ]
    for content, status_attr, reason_attr, body_word in cases:
        frame = await _through_claim_hook(content)
        head, *body, tail = frame.split("\n")
        assert status_attr in head and reason_attr in head
        assert body_word in "\n".join(body)
        assert tail == "</inbox_message>"


async def test_render_escapes_status_and_reason():
    item = InboxItem.from_row(
        {
            "id": 1,
            "target_kind": "issue",
            "target_id": 7,
            "kind": "subagent_result",
            "content": {"status": 'x"><evil', "settle_reason": "lost", "summary": "s"},
            "created_at": None,
        }
    )
    head = render_inbox_message(item).split("\n")[0]
    assert "<evil" not in head and 'status="x&quot;&gt;&lt;evil"' in head


# ── review M1: an already-claimed result orders no wake-up ─────────────────


_CLAIMED_AT = "2026-09-26T01:02:03+00:00"


async def test_reaper_orders_no_wake_when_the_result_was_already_claimed(reaper):
    """The worker filed the result and died; the drain's turn already read it.
    Waking the issue again would buy a billed "Continue" on an empty inbox."""
    stored = {
        "id": 55,
        "claimed_at": _CLAIMED_AT,
        "content": {
            "child_run_id": "9001",
            "status": "success",
            "settle_reason": "producer",
            "summary": "done it",
        },
    }
    reaper({"id": "9001", "status": "completed", "error_code": None}, stored=stored)

    out = await st.reap_stale_workforce_tasks()

    assert out["done"] == 1
    assert out["wake_orders"] == []


async def test_a_replay_whose_result_was_already_claimed_orders_no_wake():
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(
        return_value={"id": "52", "status": "completed", "output_summary": "ok"}
    )
    w.inbox_repo.enqueue = AsyncMock(
        return_value={"id": 1, "claimed_at": _CLAIMED_AT, "content": {}}
    )

    out = await _run(w, _task())

    w.run_bg.assert_not_awaited()
    assert out["idle_dispatch"] is None


async def test_an_unclaimed_replayed_result_still_wakes_the_issue():
    """Positive control: the wake is withheld only for a consumed result."""
    w = _wire()
    w.workforce.latest_run_for_task = AsyncMock(
        return_value={"id": "52", "status": "completed", "output_summary": "ok"}
    )
    w.inbox_repo.enqueue = AsyncMock(
        return_value={"id": 1, "claimed_at": None, "content": {}}
    )

    out = await _run(w, _task())

    assert out["idle_dispatch"]["issue_id"] == 7
