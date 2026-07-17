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

import pytest

from app.services.issues.comment_trigger import (
    CommentTriggerVerdict,
    compute_comment_trigger,
    is_note_comment,
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


# ── /note prefix rule (is_note_comment) ────────────────────────────────────


@pytest.mark.parametrize(
    "body,expected",
    [
        # canonical: /note followed by a space
        ("/note remember this", True),
        # /note followed by a tab
        ("/note\tremember", True),
        # /note followed by a newline
        ("/note\nremember", True),
        # /note alone (followed by end of string)
        ("/note", True),
        # /note with trailing whitespace only
        ("/note ", True),
        # leading whitespace before /note is ignored
        ("   /note trim me", True),
        # leading newline before /note
        ("\n/note", True),
        # NOT a note: /notex — the token must end at a boundary
        ("/notex is a comment", False),
        ("/noted", False),
        ("/notes", False),
        # NOT a note: /note not at the start (mid-body)
        ("please /note this", False),
        # NOT a note: case matters (verbatim match)
        ("/NOTE loud", False),
        ("/Note titled", False),
        # NOT a note: empty / whitespace / None
        ("", False),
        ("   ", False),
        (None, False),
        # NOT a note: a bare slash or different command
        ("/", False),
        ("/n", False),
        ("note without slash", False),
    ],
)
def test_is_note_comment_rule(body, expected):
    assert is_note_comment(body) is expected


def test_note_body_flips_wake_but_keeps_agent_id():
    """A /note body on an agent-assigned issue: will_wake False, is_note True,
    agent_id still populated so the chip can name who was NOT woken."""
    v = compute_comment_trigger(
        {"id": 1, "assignee_agent_id": "agent-1"}, "/note quiet"
    )
    assert v.will_wake is False
    assert v.is_note is True
    assert v.agent_id == "agent-1"


def test_non_note_body_wakes_as_before():
    v = compute_comment_trigger(
        {"id": 1, "assignee_agent_id": "agent-1"}, "change the intro"
    )
    assert v.will_wake is True
    assert v.is_note is False
    assert v.agent_id == "agent-1"


def test_none_body_defaults_to_assignee_verdict():
    """A bodyless preview (armed composer, nothing typed) still returns the
    assignee-based wake, with is_note False."""
    v = compute_comment_trigger({"id": 1, "assignee_agent_id": "agent-1"})
    assert v.will_wake is True
    assert v.is_note is False


def test_note_body_on_unassigned_issue_is_note_but_no_agent():
    """No assignee → nothing wakes regardless, but is_note still reflects the
    body so a future caller isn't surprised."""
    v = compute_comment_trigger({"id": 1, "assignee_agent_id": None}, "/note hi")
    assert v.will_wake is False
    assert v.is_note is True
    assert v.agent_id is None
