"""SetAcceptanceCriteria — the agent records checkable completion criteria
when the issue has none (issue completion loop, spec §5.1).

Exposed only on issue turns (same injection point as FinishIssue /
ScheduleWakeup). A person's criteria (source='user') are locked; the agent
may replace its own earlier proposal. At most one successful call per turn.
The handler never raises — every refusal is a typed ``{"error": ...}`` the
model reads.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.schemas.issue import ACCEPTANCE_CRITERIA_MAX_CHARS
from app.services.issues.acceptance_criteria import (
    load_acceptance_criteria,
    write_agent_criteria,
)

SET_ACCEPTANCE_CRITERIA_TOOL_NAME = "SetAcceptanceCriteria"

_DESCRIPTION = (
    "Record the acceptance criteria for this issue before you start working, "
    "when the issue has none yet. Write concrete, checkable outcomes: how many "
    "scenes or shots, whether images are generated, a word-count range. A "
    "person's own criteria are locked and cannot be changed by you. Call this "
    "at most once per turn."
)


def set_acceptance_criteria_spec() -> dict[str, Any]:
    """OpenAI function-calling spec for SetAcceptanceCriteria (model-facing)."""
    return {
        "type": "function",
        "function": {
            "name": SET_ACCEPTANCE_CRITERIA_TOOL_NAME,
            "description": _DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "criteria": {
                        "type": "string",
                        "description": (
                            "The completion criteria, as a short checklist a "
                            f"reviewer can verify. At most {ACCEPTANCE_CRITERIA_MAX_CHARS} characters."
                        ),
                    }
                },
                "required": ["criteria"],
            },
        },
    }


def make_set_acceptance_criteria_handler(
    *, issue_id: int, agent_id: Optional[str]
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Per-turn handler bound to one issue and the agent working it."""
    accepted: Optional[str] = None

    async def handler(args: dict[str, Any], recorder: Any = None) -> dict[str, Any]:
        nonlocal accepted
        _ = recorder  # the write is keyed by issue, not by run
        criteria = str((args or {}).get("criteria") or "").strip()
        if not criteria:
            return {"error": "criteria_required"}
        if len(criteria) > ACCEPTANCE_CRITERIA_MAX_CHARS:
            return {
                "error": "criteria_too_long",
                "max_chars": ACCEPTANCE_CRITERIA_MAX_CHARS,
            }
        if accepted is not None:
            return {"error": "criteria_already_set", "criteria": accepted}
        try:
            current, source = await load_acceptance_criteria(issue_id)
        except Exception as exc:  # noqa: BLE001 — a result the model reads
            logger.opt(exception=True).warning(
                f"[SetAcceptanceCriteria] issue {issue_id}: read failed: {exc}"
            )
            return {"error": "criteria_write_failed"}
        if current and source == "user":
            return {"error": "criteria_locked", "criteria": current}
        try:
            await write_agent_criteria(issue_id, criteria, agent_id=agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(
                f"[SetAcceptanceCriteria] issue {issue_id}: write failed: {exc}"
            )
            return {"error": "criteria_write_failed"}
        accepted = criteria
        return {"ok": True, "criteria": criteria, "source": "agent"}

    return handler


__all__ = [
    "ACCEPTANCE_CRITERIA_MAX_CHARS",
    "SET_ACCEPTANCE_CRITERIA_TOOL_NAME",
    "make_set_acceptance_criteria_handler",
    "set_acceptance_criteria_spec",
]
