"""The comment-path trigger predicate — the single source of truth for
"does posting a comment wake an agent".

Unlike dispatch_preview (which hand-mirrors dispatch_issue's guards), the
comment predicate lives in ONE function that both POST /messages and
GET /comment-trigger-preview call, so the chip can never claim something
the send path won't do.

The rule today is deliberately thin — an assigned agent wakes, full stop.
No status guard: commenting on a `done` issue DOES wake the agent, matching
multica's deliberate design (comments are conversational at any stage).
These tests pin that thinness so a future guard is a conscious edit.
"""

from __future__ import annotations

from app.services.issues.comment_trigger import (
    CommentTriggerVerdict,
    compute_comment_trigger,
)


def test_wakes_when_agent_assigned():
    v = compute_comment_trigger(
        {"id": 1, "assignee_agent_id": "11111111-1111-1111-1111-111111111111"}
    )
    assert v.will_wake is True
    assert v.agent_id == "11111111-1111-1111-1111-111111111111"


def test_no_wake_without_assignee():
    v = compute_comment_trigger({"id": 1, "assignee_agent_id": None})
    assert v.will_wake is False
    assert v.agent_id is None


def test_no_wake_when_key_absent():
    """A row shaped without the column at all must not blow up."""
    v = compute_comment_trigger({"id": 1})
    assert v.will_wake is False
    assert v.agent_id is None


def test_snowflake_agent_id_stays_a_string():
    """Snowflake ids exceed 2^53 — the verdict must never hand the frontend a
    number to lose precision on (see feedback_asyncpg_bigint_str_strict)."""
    v = compute_comment_trigger({"id": 1, "assignee_agent_id": 7284531902847561234})
    assert v.agent_id == "7284531902847561234"
    assert isinstance(v.agent_id, str)


def test_wakes_on_terminal_status():
    """No status guard by design: a comment on a done issue still wakes the
    agent. If this ever changes it must be a deliberate product decision, not
    an accident — so pin today's truth."""
    v = compute_comment_trigger(
        {"id": 1, "assignee_agent_id": "agent-1", "status": "done"}
    )
    assert v.will_wake is True


def test_wakes_while_a_run_is_live():
    """Unlike dispatch_preview's ALREADY_RUNNING guard, the comment path has no
    such check — a reply mid-run still dispatches another turn."""
    v = compute_comment_trigger(
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": "in_progress",
            "dbos_workflow_id": "issue-1-live",
        }
    )
    assert v.will_wake is True


def test_verdict_is_immutable():
    v = compute_comment_trigger({"id": 1, "assignee_agent_id": "agent-1"})
    assert isinstance(v, CommentTriggerVerdict)
    try:
        v.will_wake = False  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("verdict must be frozen")


def test_suppression_is_subtractive():
    """The client names which agent to skip; a non-matching id must NOT
    suppress. Mirrors multica's filterSuppressedCommentAgentTriggers — the
    client can only remove from the server's set, never add to it."""
    from app.services.issues.comment_trigger import apply_suppression

    v = compute_comment_trigger({"id": 1, "assignee_agent_id": "agent-1"})

    assert apply_suppression(v, ["agent-1"]).will_wake is False
    assert apply_suppression(v, ["someone-else"]).will_wake is True
    assert apply_suppression(v, []).will_wake is True
    assert apply_suppression(v, None).will_wake is True


def test_suppression_keeps_agent_id_for_display():
    """Suppressed still reports WHO was suppressed — the caller renders
    'Won't start this time · {agent}' and the note path needs the id."""
    from app.services.issues.comment_trigger import apply_suppression

    v = compute_comment_trigger({"id": 1, "assignee_agent_id": "agent-1"})
    s = apply_suppression(v, ["agent-1"])
    assert s.will_wake is False
    assert s.agent_id == "agent-1"


def test_suppression_on_a_no_wake_verdict_is_a_noop():
    """Unassigned issue + a stray suppress list → still no wake, no crash."""
    from app.services.issues.comment_trigger import apply_suppression

    v = compute_comment_trigger({"id": 1, "assignee_agent_id": None})
    s = apply_suppression(v, ["agent-1"])
    assert s.will_wake is False
    assert s.agent_id is None
