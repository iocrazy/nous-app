"""The comment path's agent-trigger predicate — one function, two callers.

Posting a comment on an issue with an assigned agent starts a billed agent
turn. `POST /issues/{id}/messages` and `GET /issues/{id}/comment-trigger-preview`
both route their decision through here, so the composer chip can never promise
something the send path won't do.

Deliberately NOT reusing issues_router.dispatch_preview: that predicate mirrors
`dispatch_issue`'s guards (dbos_disabled / terminal_status / already_running),
and the comment path honours none of them — a comment on a `done` issue, or one
posted mid-run, still wakes the agent. Rendering the chip from the dispatch
predicate would state the opposite of what happens.

Suppression is subtractive: the client names which agent to skip and the server
can only remove from the set it computed itself. A client can never add a
trigger. (Mirrors multica's filterSuppressedCommentAgentTriggers.)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence


@dataclass(frozen=True)
class CommentTriggerVerdict:
    """What posting a comment on this issue will do.

    `agent_id` is populated whenever the issue has an assigned agent — including
    when `will_wake` is False due to suppression, so callers can still name who
    was skipped and resolve the issue's session.
    """

    will_wake: bool
    agent_id: Optional[str]


def compute_comment_trigger(issue_row: dict) -> CommentTriggerVerdict:
    """The rule, verbatim from issue_messages_router's `if assignee_agent_id:`.

    Thin on purpose. No status guard, no live-run guard, no DBOS-enabled guard —
    the send path checks none of those, and this must describe what the send path
    actually does, not what it arguably should.
    """
    agent_raw = issue_row.get("assignee_agent_id")
    if not agent_raw:
        return CommentTriggerVerdict(will_wake=False, agent_id=None)
    # str() is load-bearing: agent ids are Snowflake bigints and JSON numbers
    # past 2^53 lose precision in the browser.
    return CommentTriggerVerdict(will_wake=True, agent_id=str(agent_raw))


def apply_suppression(
    verdict: CommentTriggerVerdict,
    suppress_agent_ids: Optional[Sequence[str]],
) -> CommentTriggerVerdict:
    """Drop the wake if the client explicitly named this agent.

    A non-matching id is a no-op by design: if the assignee changed between the
    preview and the send, the user's "skip agent A" must not silently swallow a
    wake for agent B they never saw.
    """
    if not verdict.will_wake or not suppress_agent_ids:
        return verdict
    if verdict.agent_id in {str(a) for a in suppress_agent_ids}:
        return replace(verdict, will_wake=False)
    return verdict
