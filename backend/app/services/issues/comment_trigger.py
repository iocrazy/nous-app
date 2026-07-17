"""The comment path's agent-trigger predicate — one function, two callers.

Posting a comment on an issue with an assigned agent starts a billed agent
turn. `POST /issues/{id}/messages` and `POST /issues/{id}/comment-trigger-preview`
both route their decision through here, so the composer chip can never promise
something the send path won't do.

Deliberately NOT reusing issues_router.dispatch_preview: that predicate mirrors
`dispatch_issue`'s guards (dbos_disabled / terminal_status / already_running),
and the comment path honours none of them — a comment on a `done` issue, or one
posted mid-run, still wakes the agent. Rendering the chip from the dispatch
predicate would state the opposite of what happens.

Two inputs make `will_wake` False even with an assigned agent:
  * Suppression (subtractive): the client names which agent to skip and the
    server can only remove from the set it computed itself. A client can never
    add a trigger. (Mirrors multica's filterSuppressedCommentAgentTriggers.)
  * The `/note` prefix (keyboard flow): a comment body that opens with `/note`
    (followed by whitespace or end of string) is a silent note — it lands in
    the session for the agent to read on its next wake but wakes nothing now.
    Mirrors multica's isNoteComment. The prefix is matched verbatim — case
    matters (`/NOTE` is not a note) and `/notex` is not a note.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

# The keyboard flow's marker. Matched literally (case-sensitive) — see
# is_note_comment for the "followed by whitespace or end" rule.
_NOTE_PREFIX = "/note"


def is_note_comment(body: Optional[str]) -> bool:
    """True when *body* is a silent `/note` command.

    Rule (single source of truth for the whole app — the frontend mirrors it
    only to decide WHEN to re-ask the server, never to render the verdict):
      * leading whitespace is ignored;
      * the remainder must start with the literal ``/note`` (case-sensitive);
      * ``/note`` must be followed by whitespace (space / tab / newline) OR the
        end of the string — so ``/notex`` is a normal comment, not a note.
    """
    if not body:
        return False
    stripped = body.lstrip()
    if not stripped.startswith(_NOTE_PREFIX):
        return False
    rest = stripped[len(_NOTE_PREFIX) :]
    return rest == "" or rest[0].isspace()


@dataclass(frozen=True)
class CommentTriggerVerdict:
    """What posting a comment on this issue will do.

    `agent_id` is populated whenever the issue has an assigned agent — including
    when `will_wake` is False due to suppression or a `/note` prefix, so callers
    can still name who was skipped and resolve the issue's session.

    `is_note` records that the body was a `/note` command. It is the reason
    `will_wake` is False (as opposed to suppression), so the chip can render the
    quiet-note state and still name the agent that was NOT woken.
    """

    will_wake: bool
    agent_id: Optional[str]
    is_note: bool = False


def compute_comment_trigger(
    issue_row: dict, body: Optional[str] = None
) -> CommentTriggerVerdict:
    """Decide whether posting *body* on this issue wakes its assigned agent.

    Thin on purpose. No status guard, no live-run guard, no DBOS-enabled guard —
    the send path checks none of those, and this must describe what the send path
    actually does, not what it arguably should.

    `body` is optional so a bodyless caller (e.g. an armed preview before the
    user types) still gets the assignee-based verdict. A `/note` body flips
    `will_wake` to False while keeping `agent_id` for display.
    """
    note = is_note_comment(body)
    agent_raw = issue_row.get("assignee_agent_id")
    if not agent_raw:
        return CommentTriggerVerdict(will_wake=False, agent_id=None, is_note=note)
    # str() is load-bearing: agent ids are Snowflake bigints and JSON numbers
    # past 2^53 lose precision in the browser.
    agent_id = str(agent_raw)
    if note:
        # A note never wakes, but still names WHO it won't wake.
        return CommentTriggerVerdict(will_wake=False, agent_id=agent_id, is_note=True)
    return CommentTriggerVerdict(will_wake=True, agent_id=agent_id, is_note=False)


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
