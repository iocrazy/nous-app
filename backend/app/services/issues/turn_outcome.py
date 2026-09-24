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
from app.services.ai.tools.finish_issue_tool import extract_issue_outcome

#: ``question.kind`` the budget gate parks with (``budget_hook._ask``).
BUDGET_QUESTION_KIND = "budget"
#: Declarations that end the work, so a budget park after them is moot.
_WORK_DONE_OUTCOMES = frozenset({"completed"})
#: A cancelled turn's ``completed`` becomes this: in_review, never done.
_CANCELLED_COMPLETED_AS = "continue"


class TurnOutcome(NamedTuple):
    outcome: Optional[str]
    reason: Optional[str]
    question: Optional[dict[str, Any]]
    awaiting_input: bool


def resolve_turn_outcome(result: dict[str, Any]) -> TurnOutcome:
    """Declaration first, a park overrides it, a budget park yields to a
    declared ``completed``, a human cancel demotes ``completed`` to
    ``continue``. See the module docstring."""
    outcome, reason = extract_issue_outcome(result.get("tool_calls"))
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


__all__ = ["BUDGET_QUESTION_KIND", "TurnOutcome", "resolve_turn_outcome"]
