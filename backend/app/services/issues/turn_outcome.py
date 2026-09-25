"""One issue turn's outcome, read off ``run_session_turn``'s result.

Shared by the two issue turn entry points — ``issue_agent_executor.run_issue_agent``
(dispatch) and ``issue_lifecycle.run_issue_reply_step`` (reply) — which used to
carry identical copies of the extract-then-park lines. Both run inside a
``@DBOS.step``; this module is plain synchronous code with no I/O.

Precedence (fh2 T4):

1. The agent's last acknowledged FinishIssue declaration (``tool_calls``).
2. A parked question overrides it — the turn stopped on something a human must
   answer (``awaiting_input_outcome`` → ``needs_input``).
3. **Except** a budget park after a declared ``completed``. The budget gate
   halts at a step BOUNDARY, i.e. before the next LLM call; a ``completed``
   declared in an earlier step means the work is done and there is nothing
   left to spend on. Production run 347463748025273 (2026-09-08): FinishIssue
   (completed) at step 1, ``budget_check{halt}`` at step 2 — the issue went to
   needs_followup. ``continue`` / ``needs_input`` declarations both lead to more
   spend, so the budget question stands for those; an AskUser (``kind: user``)
   is the agent's own later word and always stands.
4. A human cancel (``stop_reason == "cancelled"``) downgrades a declared
   ``completed`` to ``continue``: a person explicitly said stop, so a person
   reviews — the issue must never auto-close to ``done``. The workflow's
   cancelled branch breaks out of the dispatch loop, so ``continue`` takes the
   capped route in ``route_finish_outcome``: ``in_review``, never ``done``
   regardless of ``auto_close`` (review MEDIUM-1). Other declarations pass
   through unchanged.
5. A max-iterations exit (``exit_code``, fh3 T4 ruling A′) whose trace has a
   non-FinishIssue tool AFTER the last FinishIssue: the agent declared, then
   kept working until the ceiling, so the declaration went stale and the turn
   is ``continue`` (reason prefixed ``declaration_stale_after_tools``).
   FinishIssue re-declared in a loop (run 347463025060485) is not "kept
   working": the last declaration stands. ``run_timeout`` and abort mid-call
   route by the declaration as-is (ruling A); both end at a step boundary.
6. An approval exit (``awaiting_approval``, fh3 T5 ruling 10) beats every
   declaration, at the same tier as a park: a pre-hook approval means the gated
   tool never ran, so an earlier ``completed`` must not close the issue. The
   true-stream path carries the trace and would otherwise route by it; the
   buffered path drops the trace but emits a non-empty bracket line, which the
   executor used to answer with a forced declaration. The turn routes to
   ``needs_followup`` with reason ``awaiting_approval: <reason>`` and is not
   parked (no approval producer exists yet); when approvals are built this
   becomes a real park.

The budget question the gate recorded on the run stays in that run's
transcript (``question_asked`` with no ``question_answered``), and the chat
service also keeps a copy in the assistant message's
``metadata_json.awaiting_input``. The issue is not parked on it, so no feed
lists it and no answer is expected; a future reader of that metadata that does
not distinguish issue sessions would surface it (ticketed).
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from app.services.ai.tools.ask_user_tool import awaiting_input_outcome
from app.services.ai.tools.finish_issue_tool import (
    FINISH_ISSUE_TOOL_NAME,
    extract_issue_outcome,
)

#: ``question.kind`` the budget gate parks with (``budget_hook._ask``).
BUDGET_QUESTION_KIND = "budget"
#: Declarations that end the work, so a budget park after them is moot.
_WORK_DONE_OUTCOMES = frozenset({"completed"})
#: A cancelled turn's ``completed`` becomes this: in_review, never done.
_CANCELLED_COMPLETED_AS = "continue"
#: ``run_session_turn``'s ``exit_code`` for the iteration ceiling, buffered
#: (``run_turn``) and true-stream spelling.
MAX_ITERATION_EXITS = frozenset(
    {"max_tool_iterations_exceeded", "max_stream_iterations_exceeded"}
)
#: What a stale declaration becomes, and the prefix of its reason.
_STALE_DECLARATION_AS = "continue"
STALE_DECLARATION_REASON = "declaration_stale_after_tools"
#: An approval exit's outcome (``route_finish_outcome`` → status
#: needs_followup, ``agent_outcome`` awaiting_approval) and reason prefix.
AWAITING_APPROVAL_OUTCOME = "needs_followup"
AWAITING_APPROVAL_REASON_PREFIX = "awaiting_approval: "
#: Same default the buffered branch prints when the hook gave no reason.
_DEFAULT_APPROVAL_REASON = "approval required"


class TurnOutcome(NamedTuple):
    outcome: Optional[str]
    reason: Optional[str]
    question: Optional[dict[str, Any]]
    awaiting_input: bool


def resolve_turn_outcome(result: dict[str, Any]) -> TurnOutcome:
    """Declaration first (demoted to ``continue`` when a max-iterations exit
    left it stale), a park overrides it, a budget park yields to a declared
    ``completed``, a human cancel demotes ``completed`` to ``continue``, and an
    approval exit beats them all. See the module docstring."""
    if result.get("awaiting_approval"):
        return _awaiting_approval_outcome(result)
    outcome, reason = extract_issue_outcome(result.get("tool_calls"))
    if outcome not in (None, _STALE_DECLARATION_AS) and _declaration_went_stale(result):
        reason = (
            f"{STALE_DECLARATION_REASON}: declared {outcome!r} "
            f"({reason or 'no reason'}), then kept calling tools until the "
            f"iteration limit"
        )
        outcome = _STALE_DECLARATION_AS
    if result.get("stop_reason") == "cancelled" and outcome == "completed":
        outcome = _CANCELLED_COMPLETED_AS
    parked = awaiting_input_outcome(result)
    if parked is None:
        return TurnOutcome(outcome, reason, None, False)
    _, park_reason, question = parked
    if (
        outcome in _WORK_DONE_OUTCOMES
        and (question or {}).get("kind") == BUDGET_QUESTION_KIND
    ):
        return TurnOutcome(outcome, reason, None, False)
    return TurnOutcome("needs_input", park_reason, question, True)


def _awaiting_approval_outcome(result: dict[str, Any]) -> TurnOutcome:
    """Rule 6: never routed by a declaration, never parked (yet)."""
    why = str(result.get("approval_reason") or "") or _DEFAULT_APPROVAL_REASON
    return TurnOutcome(
        AWAITING_APPROVAL_OUTCOME,
        f"{AWAITING_APPROVAL_REASON_PREFIX}{why}",
        None,
        False,
    )


def _declaration_went_stale(result: dict[str, Any]) -> bool:
    """Ruling A′: only on a max-iterations exit, and only when a tool other
    than FinishIssue ran after the last FinishIssue in the trace."""
    if result.get("exit_code") not in MAX_ITERATION_EXITS:
        return False
    names = [c.get("name") for c in (result.get("tool_calls") or [])]
    if FINISH_ISSUE_TOOL_NAME not in names:
        return False
    last = len(names) - 1 - names[::-1].index(FINISH_ISSUE_TOOL_NAME)
    return any(n != FINISH_ISSUE_TOOL_NAME for n in names[last + 1 :])


__all__ = [
    "AWAITING_APPROVAL_OUTCOME",
    "AWAITING_APPROVAL_REASON_PREFIX",
    "BUDGET_QUESTION_KIND",
    "MAX_ITERATION_EXITS",
    "STALE_DECLARATION_REASON",
    "TurnOutcome",
    "resolve_turn_outcome",
]
