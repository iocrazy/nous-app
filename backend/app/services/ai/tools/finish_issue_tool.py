"""FinishIssue — the tool an issue-executing agent calls to DECLARE the outcome
of its turn (Workforce Spec-2, paperclip ``classifyRunLiveness`` analogue).

The agent self-reports one of:
  * ``completed``    — the task is done; a human can review/close it.
  * ``needs_input``  — blocked on a human decision/info; stop and ask.
  * ``continue``     — real progress made but more work remains; the
                       orchestrator may re-run the agent (bounded).

This tool has NO side effects of its own — the declaration is captured in the
turn's ``tool_calls`` trace (which ``run_session_turn`` already returns) and the
``execute_issue`` workflow reads it to drive issue status + bounded
continuation. Keeping it side-effect-free means the chat runtime stays unaware
of issue semantics; only the issue workflow interprets the signal.

The tool is exposed ONLY on issue-context turns (``trigger`` in
``issue_dispatch`` / ``issue_reply``); regular chat turns never see it.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.ai.runner.question import OPTIONS_JSON_SCHEMA

# Allowed self-reported outcomes. Kept as a tuple so it can seed both the JSON
# schema enum (model-facing) and the orchestrator's validation.
FINISH_ISSUE_OUTCOMES: tuple[str, ...] = ("completed", "needs_input", "continue")

# The tool name as the model sees it / the runner dispatches on.
FINISH_ISSUE_TOOL_NAME = "FinishIssue"

# System-prompt directive appended to issue-context turns so the agent reliably
# DECLARES an outcome. Without this the tool is latent — agents rarely self-close
# from the spec description alone, and the workflow falls back to in_review.
FINISH_ISSUE_INSTRUCTION = (
    "You are working an assigned issue. Before you end this turn you MUST call "
    "the FinishIssue tool exactly once to declare the outcome:\n"
    "- 'completed' — the task is done and ready for a human to review.\n"
    "- 'needs_input' — you are blocked and need a human decision or information; "
    "state precisely what you need in 'reason'.\n"
    "- 'continue' — you made real progress but need another turn to finish.\n"
    "Always include a one-sentence 'reason'. Do not end the turn without calling "
    "FinishIssue."
)


def finish_issue_spec() -> dict[str, Any]:
    """OpenAI function-calling spec for FinishIssue (model-facing)."""
    return {
        "type": "function",
        "function": {
            "name": FINISH_ISSUE_TOOL_NAME,
            "description": (
                "Declare the outcome of your work on this issue. Call this once "
                "when you are done with the turn: use 'completed' if the task is "
                "finished, 'needs_input' if you are blocked and need a human "
                "decision or information, or 'continue' if you made progress but "
                "need another turn to finish. Always include a short 'reason'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": list(FINISH_ISSUE_OUTCOMES),
                        "description": "completed | needs_input | continue",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "One sentence: what was done, or what you need from "
                            "a human, or what remains."
                        ),
                    },
                    # Phase 2a: a needs_input declaration may offer choices —
                    # same sub-schema as AskUser.options.
                    "options": OPTIONS_JSON_SCHEMA,
                },
                "required": ["outcome"],
            },
        },
    }


async def finish_issue_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Validate + acknowledge a FinishIssue call. Returns a tool-result dict
    (never raises) — the orchestrator reads the real signal from the trace.

    An invalid/missing outcome returns an error result so the model can
    self-correct on the next iteration rather than silently mis-declaring."""
    outcome = str(args.get("outcome") or "").strip()
    if outcome not in FINISH_ISSUE_OUTCOMES:
        return {
            "error": (
                f"Invalid outcome {outcome!r}. Must be one of "
                f"{list(FINISH_ISSUE_OUTCOMES)}."
            )
        }
    reason = str(args.get("reason") or "").strip()
    out: dict[str, Any] = {"acknowledged": True, "outcome": outcome, "reason": reason}
    options = args.get("options")
    if isinstance(options, list) and options:
        out["options"] = options
    return out


def extract_issue_outcome(
    tool_calls: Optional[list[dict[str, Any]]],
) -> tuple[Optional[str], Optional[str]]:
    """Scan a turn's ``tool_calls`` trace for the LAST valid FinishIssue
    declaration and return ``(outcome, reason)``.

    Only declarations the handler accepted (``result.acknowledged``) count, so a
    rejected/malformed call never drives status. Returns ``(None, None)`` when
    the agent never declared — the workflow then falls back to its default
    (``in_review``)."""
    if not tool_calls:
        return None, None
    for call in reversed(tool_calls):
        if call.get("name") != FINISH_ISSUE_TOOL_NAME:
            continue
        result = call.get("result")
        if isinstance(result, dict) and result.get("acknowledged"):
            outcome = result.get("outcome")
            if outcome in FINISH_ISSUE_OUTCOMES:
                return outcome, result.get("reason")
        # Fall back to the raw args if the trace stored only those.
        args = call.get("args")
        if isinstance(args, dict):
            outcome = str(args.get("outcome") or "").strip()
            if outcome in FINISH_ISSUE_OUTCOMES:
                return outcome, str(args.get("reason") or "").strip() or None
    return None, None


def extract_issue_options(
    tool_calls: Optional[list[dict[str, Any]]],
) -> Optional[list[dict[str, Any]]]:
    """The ``options`` of the LAST acknowledged ``needs_input`` declaration
    (raw, not yet normalised), or None. Mirrors ``extract_issue_outcome``."""
    if not tool_calls:
        return None
    for call in reversed(tool_calls):
        if call.get("name") != FINISH_ISSUE_TOOL_NAME:
            continue
        result = call.get("result")
        args = call.get("args")
        outcome = None
        if isinstance(result, dict) and result.get("acknowledged"):
            outcome = result.get("outcome")
        elif isinstance(args, dict):
            outcome = str(args.get("outcome") or "").strip()
        if outcome != "needs_input":
            return None
        for src in (result, args):
            if isinstance(src, dict) and isinstance(src.get("options"), list):
                return src["options"] or None
        return None
    return None


__all__ = [
    "FINISH_ISSUE_OUTCOMES",
    "FINISH_ISSUE_TOOL_NAME",
    "FINISH_ISSUE_INSTRUCTION",
    "finish_issue_spec",
    "finish_issue_handler",
    "extract_issue_outcome",
    "extract_issue_options",
]
