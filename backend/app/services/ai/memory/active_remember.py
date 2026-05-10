"""Active remember tool — agent emits explicit "remember this" calls.

Wave 5d (M2.D). Today memory is 100% passive: a background harvester
extracts facts from chat history. The agent has no way to say "this
matters, save it precisely" — it just hopes the harvester later guesses
right.

This module provides the tool surface the agent can invoke:

  remember(summary: str, when_to_use: str, scope: str = 'agent_user') → dict

Wires into:
  - MCP tool descriptor (skill_to_tool wrapper) for external clients
  - SkillToolService for in-process chat (the agent's main path)
  - writer.py with extracted_from='active_call' (M2.D constraint update)

Schema validation:
  - summary: 5-500 chars (too short = useless, too long = should consolidate first)
  - when_to_use: 5-200 chars (the embedding source per RemiMem pattern)
  - scope: must match agent_memories.scope CHECK enum
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

# Mirrors agent_memories.scope CHECK constraint (migration 156).
ALLOWED_SCOPES = frozenset(
    {"session", "agent_user", "user_global", "team_agent", "root_tree"}
)
DEFAULT_SCOPE = "agent_user"

MIN_SUMMARY_LEN = 5
MAX_SUMMARY_LEN = 500
MIN_WHEN_LEN = 5
MAX_WHEN_LEN = 200


class RememberValidationError(ValueError):
    """Raised when the agent passes malformed args to remember()."""


@dataclass(frozen=True)
class RememberRequest:
    """Validated args ready for writer.write()."""

    summary: str
    when_to_use: str
    scope: str
    extracted_from: str = "active_call"


def validate_remember_args(
    *,
    summary: str,
    when_to_use: str,
    scope: str = DEFAULT_SCOPE,
) -> RememberRequest:
    """Validate the arguments. Raises ``RememberValidationError`` on
    any rule violation. Caller (the tool dispatcher) should catch and
    return ToolCallResult.text(..., is_error=True)."""
    if not isinstance(summary, str):
        raise RememberValidationError("summary must be a string")
    if not isinstance(when_to_use, str):
        raise RememberValidationError("when_to_use must be a string")
    summary = summary.strip()
    when_to_use = when_to_use.strip()
    if len(summary) < MIN_SUMMARY_LEN:
        raise RememberValidationError(
            f"summary too short ({len(summary)} chars; min {MIN_SUMMARY_LEN})"
        )
    if len(summary) > MAX_SUMMARY_LEN:
        raise RememberValidationError(
            f"summary too long ({len(summary)} chars; max {MAX_SUMMARY_LEN}). "
            "Consider splitting into multiple memories or letting consolidation merge later."
        )
    if len(when_to_use) < MIN_WHEN_LEN:
        raise RememberValidationError(
            f"when_to_use too short ({len(when_to_use)} chars; min {MIN_WHEN_LEN})"
        )
    if len(when_to_use) > MAX_WHEN_LEN:
        raise RememberValidationError(
            f"when_to_use too long ({len(when_to_use)} chars; max {MAX_WHEN_LEN})"
        )
    if scope not in ALLOWED_SCOPES:
        raise RememberValidationError(
            f"scope must be one of {sorted(ALLOWED_SCOPES)}; got {scope!r}"
        )
    return RememberRequest(
        summary=summary,
        when_to_use=when_to_use,
        scope=scope,
    )


# ─── Handler — wires validate + writer ───────────────────────────────


# Caller-injected: takes a validated RememberRequest + identity context,
# returns the new memory id (str) or None on failure.
RememberPersistor = Callable[
    [RememberRequest, "RememberContext"],
    Awaitable[Optional[str]],
]


@dataclass(frozen=True)
class RememberContext:
    """Identity + scope-resolution context for one remember() call."""

    agent_id: str
    user_id: Optional[str]
    session_id: Optional[str]
    run_id: Optional[str] = None


@dataclass(frozen=True)
class RememberResult:
    """Outcome of a remember() invocation."""

    success: bool
    memory_id: Optional[str] = None
    error: Optional[str] = None


async def handle_remember(
    *,
    summary: str,
    when_to_use: str,
    scope: str = DEFAULT_SCOPE,
    context: RememberContext,
    persistor: RememberPersistor,
) -> RememberResult:
    """Validate args + persist via the injected writer. Never raises —
    returns RememberResult so the tool dispatcher can build a clean
    ToolCallResult either way."""
    try:
        request = validate_remember_args(
            summary=summary, when_to_use=when_to_use, scope=scope
        )
    except RememberValidationError as exc:
        return RememberResult(success=False, error=str(exc))

    try:
        memory_id = await persistor(request, context)
    except Exception as exc:  # noqa: BLE001
        return RememberResult(
            success=False, error=f"persistor failed: {type(exc).__name__}: {exc}"
        )
    if memory_id is None:
        return RememberResult(success=False, error="persistor returned no id")
    return RememberResult(success=True, memory_id=memory_id)


__all__ = [
    "ALLOWED_SCOPES",
    "DEFAULT_SCOPE",
    "MAX_SUMMARY_LEN",
    "MAX_WHEN_LEN",
    "MIN_SUMMARY_LEN",
    "MIN_WHEN_LEN",
    "RememberContext",
    "RememberPersistor",
    "RememberRequest",
    "RememberResult",
    "RememberValidationError",
    "handle_remember",
    "validate_remember_args",
]
