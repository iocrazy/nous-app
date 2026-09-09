"""issue_fork.fork_run: preconditions → rebuild (origin conversation + run
events) → new session → pointer switch → marker clear → supersede → dispatch.
Every side effect is a recorded call on an injectable ``deps`` object, so the
ORDER is the contract."""

import pytest

from app.services.issues import issue_fork as f

pytestmark = pytest.mark.unit

RUN_STARTED = "2026-09-09T10:00:00+00:00"


class _Deps:
    def __init__(self):
        self.calls = []
        self.run = {
            "id": 42,
            "issue_id": 9,
            "user_id": "u",
            "agent_id": "a",
            "conversation_id": 100,
            "started_at": RUN_STARTED,
        }
        self.issue = {
            "id": 9,
            "status": "in_progress",
            "ai_session_id": 100,
            "assignee_agent_id": "a",
            "created_by_user_id": "u",
            "title": "T",
            "team_id": 1,
            "project_id": None,
            "hidden_at": None,
            "execution_state": {
                "awaiting_input": {"question_id": "q:42:4", "run_id": "42"}
            },
            "paused_at": "x",
            "execution_locked_at": None,
            "dbos_workflow_id": "wf-old",
        }
        # The run's own transcript: only THIS turn's user text + steps.
        self.events = [
            {"seq": 1, "event_type": "user", "payload": {"content": "go"}},
            {"seq": 2, "event_type": "step_start", "payload": {}},
            {"seq": 3, "event_type": "assistant", "payload": {"content": "act 1"}},
            {"seq": 4, "event_type": "step_start", "payload": {}},
        ]
        # Earlier turns live in the origin conversation; its last row is the
        # same user text the run's seq-1 event carries (appended before the
        # run started) — the seam must not show it twice.
        self.origin = [
            {"role": "user", "content": "earlier ask"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "go"},
        ]
        self.live = None

    async def get_run(self, run_id, user_id):
        return self.run

    async def get_issue(self, issue_id):
        return self.issue

    async def list_events(self, run_id, at_seq):
        self.reads = getattr(self, "reads", []) + [("events", run_id, at_seq)]
        return self.events

    async def running_root_run_id(self, issue_id, conversation_id):
        if self.live == "raise":
            raise RuntimeError("db")
        return self.live

    async def list_origin_messages(self, session_id, before):
        self.calls.append(("origin", session_id, before))
        return self.origin

    async def create_session(self, **kw):
        self.calls.append(("session", kw))
        return {"id": "200", "agent_slug": "s"}

    async def append_messages(self, session_id, messages, meta):
        self.calls.append(("messages", session_id, messages, meta))

    async def switch_session_and_mark(self, issue_id, session_id, forked_from):
        self.calls.append(("switch", issue_id, session_id, forked_from))

    async def release_parked(self, workflow_id):
        self.calls.append(("release", workflow_id))

    async def supersede_question(self, run_id, question_id):
        self.calls.append(("supersede", run_id, question_id))

    async def dispatch(self, issue_id):
        self.calls.append(("dispatch", issue_id))
        return "wf-1"

    async def restore_session(self, issue_id, session_id, paused_at):
        self.calls.append(("restore", issue_id, session_id, paused_at))


async def test_happy_path_records_every_side_effect_in_order():
    d = _Deps()
    out = await f.fork_run(42, at_seq=4, steer="be darker", user_id="u", deps=d)
    kinds = [c[0] for c in d.calls]
    # supersede comes AFTER a successful dispatch — a 503 must not leave the
    # origin's question marked superseded with nothing replacing it.
    assert kinds == ["origin", "session", "messages", "switch", "dispatch", "supersede"]
    assert d.reads == [("events", 42, 4)]
    assert d.calls[0] == ("origin", 100, RUN_STARTED)
    assert d.calls[1][1]["context_id"] == "9" and d.calls[1][1]["user_id"] == "u"
    # origin turns + this run's events up to at_seq, seam de-duplicated
    assert d.calls[2][2] == [
        {"role": "user", "content": "earlier ask"},
        {"role": "assistant", "content": "earlier reply"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "act 1"},
    ]
    assert d.calls[2][3] == {"forked_from": {"run_id": 42, "at_seq": 4}}
    assert d.calls[3] == (
        "switch",
        9,
        "200",
        {"run_id": 42, "at_seq": 4, "steer": True, "steer_text": "be darker"},
    )
    assert d.calls[5] == ("supersede", 42, "q:42:4")
    assert out == {
        "run_id": None,
        "session_id": "200",
        "workflow_id": "wf-1",
        "issue_id": 9,
        "forked_from": {"run_id": 42, "at_seq": 4},
    }


async def test_blank_steer_is_no_steer_and_another_runs_question_is_superseded_on_that_run():
    d = _Deps()
    d.issue["execution_state"] = {
        "awaiting_input": {"question_id": "q:41:4", "run_id": "41"}
    }
    await f.fork_run(42, at_seq=4, steer="   ", user_id="u", deps=d)
    # the fork abandons whatever question the issue was parked on
    assert d.calls[-1] == ("supersede", 41, "q:41:4")
    switch = next(c for c in d.calls if c[0] == "switch")
    assert switch[3] == {"run_id": 42, "at_seq": 4, "steer": False, "steer_text": None}


async def test_no_open_question_means_no_supersede():
    d = _Deps()
    d.issue["execution_state"] = {}
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert "supersede" not in [c[0] for c in d.calls]


async def test_parked_issue_is_released_before_the_switch_then_dispatched():
    """execute_issue holds execution_locked_at while PARKED on a question (its
    agent_runs row is closed, so run_live cannot see it): the fork releases
    the parked workflow — marker, cancel, lock — before dispatching."""
    d = _Deps()
    d.issue["execution_locked_at"] = "2026-09-09T10:05:00+00:00"
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    kinds = [c[0] for c in d.calls]
    assert kinds == [
        "origin",
        "session",
        "messages",
        "release",
        "switch",
        "dispatch",
        "supersede",
    ]
    assert ("release", "wf-old") in d.calls


async def test_locked_but_not_parked_is_issue_busy():
    d = _Deps()
    d.issue["execution_locked_at"] = "2026-09-09T10:05:00+00:00"
    d.issue["execution_state"] = {}  # no open question → a real running workflow
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert (ei.value.code, ei.value.status) == ("issue_busy", 409)
    assert d.calls == []


async def test_locked_with_an_answered_question_is_issue_busy_too():
    d = _Deps()
    d.issue["execution_locked_at"] = "x"
    d.issue["execution_state"]["awaiting_input"]["answered_at"] = "y"
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert ei.value.code == "issue_busy" and d.calls == []


async def test_history_comes_from_the_runs_own_conversation_not_the_issue_pointer():
    """A run forked earlier lives in a session the issue no longer points at."""
    d = _Deps()
    d.run["conversation_id"] = 77
    d.issue["ai_session_id"] = 100
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert d.calls[0] == ("origin", 77, RUN_STARTED)


async def test_a_run_window_opening_with_a_summary_replaces_the_origin_entirely():
    d = _Deps()
    d.events = [
        {
            "seq": 1,
            "event_type": "compaction_summary",
            "payload": {"summary": "S", "path": "legacy", "attempts": 1},
        },
        {"seq": 2, "event_type": "step_start", "payload": {}},
        {"seq": 3, "event_type": "assistant", "payload": {"content": "act 1"}},
        {"seq": 4, "event_type": "step_start", "payload": {}},
    ]
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    msgs = next(c for c in d.calls if c[0] == "messages")[2]
    assert msgs == [
        {"role": "system", "content": f.SUMMARY_PREFIX + "S"},
        {"role": "assistant", "content": "act 1"},
    ]


async def test_seam_is_not_deduplicated_when_the_texts_differ():
    d = _Deps()
    d.origin = [{"role": "user", "content": "something else"}]
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    msgs = next(c for c in d.calls if c[0] == "messages")[2]
    assert [m["content"] for m in msgs] == ["something else", "go", "act 1"]


async def test_no_origin_session_means_events_only():
    d = _Deps()
    d.issue["ai_session_id"] = None
    d.run["conversation_id"] = None
    await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    kinds = [c[0] for c in d.calls]
    assert "origin" not in kinds
    msgs = next(c for c in d.calls if c[0] == "messages")[2]
    assert [m["content"] for m in msgs] == ["go", "act 1"]


@pytest.mark.parametrize(
    "mutate, code, status",
    [
        (lambda d: d.run.update(issue_id=None), "not_an_issue_run", 409),
        (lambda d: setattr(d, "run", None), "not_found", 404),
        (lambda d: d.issue.update(hidden_at="x"), "not_found", 404),
        (lambda d: setattr(d, "live", 43), "run_live", 409),
        (lambda d: setattr(d, "live", "raise"), "run_state_unavailable", 503),
        (lambda d: d.issue.update(status="done"), "issue_terminal", 409),
    ],
)
async def test_preconditions(mutate, code, status):
    d = _Deps()
    mutate(d)
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert (ei.value.code, ei.value.status) == (code, status)
    assert d.calls == [], "a rejected fork must not touch anything"


async def test_at_seq_must_be_a_step_boundary():
    d = _Deps()
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=3, steer=None, user_id="u", deps=d)
    assert (ei.value.code, ei.value.status) == ("not_a_step_boundary", 400)
    assert d.calls == []


async def test_dispatch_failure_restores_the_session_pointer():
    d = _Deps()

    async def boom(issue_id):
        raise RuntimeError("dbos down")

    d.dispatch = boom
    with pytest.raises(f.ForkRejected) as ei:
        await f.fork_run(42, at_seq=4, steer=None, user_id="u", deps=d)
    assert (ei.value.code, ei.value.status) == ("dispatch_failed", 503)
    assert d.calls[-1] == ("restore", 9, 100, "x")
    assert "supersede" not in [c[0] for c in d.calls]
