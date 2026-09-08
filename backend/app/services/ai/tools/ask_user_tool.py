"""AskUser — the agent's one verb for "ask a human to pick" (harness p4
phase 2a, spec §1).

Model-facing spec + the handler the runner calls. The handler is a thin
adapter over ``runner.question.ask_question(kind="user")``: it records the
``question_asked`` event and returns a tool result the model can read
(``asked`` / ``question_id`` / ``warnings``). It never raises — a failed ask
comes back as ``{"asked": False, "error": ...}`` so the model can rephrase
instead of the turn blowing up.

Parking is the RUNNER's job: right after this tool ran (``asked`` True) the
turn ends with ``stop_reason="awaiting_input"`` on both paths — the model is
never called again to "continue" a conversation nobody is in yet.

The option sub-schema is ``question.OPTIONS_JSON_SCHEMA``, shared with
``FinishIssue.options`` so the model sees one shape in both places.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.ai.runner.question import (
    OPTIONS_JSON_SCHEMA,
    PROMPT_MAX,
    QuestionNotRecorded,
    ask_question,
)

ASK_USER_TOOL_NAME = "AskUser"


def ask_user_spec() -> dict[str, Any]:
    """OpenAI function-calling spec for AskUser (model-facing)."""
    return {
        "type": "function",
        "function": {
            "name": ASK_USER_TOOL_NAME,
            "description": (
                "Ask the human a question and stop until they answer. Use it "
                "when you cannot proceed without a decision. Give up to 6 short "
                "options when the choice is between known alternatives; the "
                "human may also type a free-text answer unless you set "
                "allow_free_text to false. Your turn ends after this call; you "
                "will receive the answer as the next user message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "maxLength": PROMPT_MAX,
                        "description": "The question, one or two sentences.",
                    },
                    "options": OPTIONS_JSON_SCHEMA,
                    "allow_free_text": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Whether the human may answer with their own text "
                            "instead of picking an option."
                        ),
                    },
                },
                "required": ["question"],
            },
        },
    }


async def ask_user_handler(
    args: dict[str, Any], *, recorder: Any, turn: int, step: int
) -> dict[str, Any]:
    """Record the question; return a tool result (never raises)."""
    prompt = str((args or {}).get("question") or "").strip()
    if not prompt:
        return {"asked": False, "error": "AskUser needs a non-empty 'question'."}
    try:
        question = await ask_question(
            recorder,
            kind="user",
            prompt=prompt,
            options=(args or {}).get("options"),
            allow_free_text=bool((args or {}).get("allow_free_text", True)),
            turn=turn,
            step=step,
        )
    except QuestionNotRecorded as exc:
        return {"asked": False, "error": f"question could not be recorded: {exc}"}
    except ValueError as exc:
        return {"asked": False, "error": str(exc)}
    from app.services.ai.runner.question import normalize_options

    _, warnings = normalize_options((args or {}).get("options"))
    return {"asked": True, "question_id": question.question_id, "warnings": warnings}


def awaiting_input_outcome(
    result: dict[str, Any],
) -> Optional[tuple[str, str, dict[str, Any]]]:
    """A turn parked on a typed question IS a ``needs_input`` declaration:
    ``(outcome, reason, question)`` — or None when the turn did not park."""
    if not result.get("awaiting_input"):
        return None
    question = result.get("question") or {}
    return "needs_input", str(question.get("prompt") or ""), question


__all__ = [
    "ASK_USER_TOOL_NAME",
    "ask_user_handler",
    "ask_user_spec",
    "awaiting_input_outcome",
]
