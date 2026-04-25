"""Hook framework for AgentRunner — PreToolUse / PostToolUse extension points.

Design (locked in plan-eng-review 2026-04-25):

- Hooks fire in the AgentRunner tool-call loop. PreToolUse runs before
  ``skill_tool.execute(args)``, PostToolUse runs after the tool result
  is appended to messages.
- Each hook returns a :class:`HookResult` with a 4-state ``decision``:
  ``continue`` / ``modify`` / ``abort`` / ``await_approval``.
- Hooks declare a ``priority`` integer; lower numbers run first.
  Same-priority hooks run in registration order.
- ``HookResult.side_effect`` is a Celery task signature (NOT a coroutine).
  AgentRunner calls ``task.delay(*args, **kwargs)`` and continues — the
  side effect runs out-of-process, OOM-isolated, retry-able. Originally
  considered ``asyncio.create_task()`` but rejected in review (memory leak
  risk on hung embedding API calls).
- Hook exceptions are caught + logged; the run continues. This is the
  contract that makes hooks safe to add — a buggy hook can't break ChatPanel.
- HookContext deliberately omits ``parent_run_id`` / ``root_run_id`` /
  ``agent_depth`` / ``delegation_chain`` — those are M2 (workforce)
  additions. M1 stays YAGNI; M2 will break the dataclass and rewrite the
  3 built-in hooks (~0.5 day).

Rendering:

    AgentRunner.run_turn
       │
       ├── Pre dispatch ── HookRegistry.get_pre_hooks()
       │       │
       │       └── for hook in sorted(by priority):
       │              result = await hook(ctx)
       │              if result.side_effect: task.delay(...)
       │              if result.decision == "abort": return aborted
       │              if result.decision == "modify": args = result.modified_args
       │              if result.decision == "await_approval": return paused
       │              # else continue
       │
       ├── tool_dispatch (existing)
       │
       └── Post dispatch ── HookRegistry.get_post_hooks() (mirror of above)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


HookDecision = Literal["continue", "modify", "abort", "await_approval"]


@dataclass(frozen=True)
class HookContext:
    """Immutable per-tool-call context handed to every hook.

    Multi-agent fields (parent_run_id / root_run_id / agent_depth /
    delegation_chain) are intentionally absent in M1. Added in M2 break
    change.
    """

    run_id: UUID
    agent_id: UUID
    agent_slug: str
    user_id: UUID
    session_id: Optional[UUID]

    tool_name: str
    tool_args: dict[str, Any]

    # Cumulative usage observed by RunRecorder before this iteration.
    # Read-only snapshot — hooks must not mutate.
    accumulated_prompt_tokens: int
    accumulated_completion_tokens: int
    accumulated_cost_cents: float

    iteration: int  # 1-indexed; first tool call in a turn = 1


@dataclass(frozen=True)
class ApprovalRequest:
    """Optional payload for HookResult.decision == 'await_approval'.

    UI surface is M4 work; M1 only persists the request shape so callers
    can pause runs and serialise the reason for later UI rendering.
    """

    reason: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    """Hook return value. Exactly one decision; auxiliary fields per case.

    Validation rules (enforced in AgentRunner, not here, so a malformed
    HookResult logs a warning and is treated as ``continue`` rather than
    crashing the run):
      - decision == "modify"          → modified_args required
      - decision == "abort"           → abort_reason required
      - decision == "await_approval"  → approval_request required
    """

    decision: HookDecision
    modified_args: Optional[dict[str, Any]] = None
    abort_reason: Optional[str] = None
    approval_request: Optional[ApprovalRequest] = None

    # Celery task signature (e.g. ``write_memory.s(run_id=...)``).
    # AgentRunner calls ``side_effect.delay()`` and continues without
    # awaiting. Out-of-process, OOM-isolated, retry-able.
    side_effect: Optional[Any] = None


# Hook protocols. Use ``Callable`` rather than methods so registries can
# accept lambdas, partials, or ``__call__`` instances uniformly.

PreToolUseHook = Callable[[HookContext], Awaitable[HookResult]]
PostToolUseHook = Callable[[HookContext, dict[str, Any]], Awaitable[HookResult]]


class _HookSpec(Protocol):
    """Internal: shape of registry entries — hook + priority + name for logs."""

    name: str
    priority: int


@dataclass(frozen=True)
class _PreEntry:
    name: str
    priority: int
    hook: PreToolUseHook


@dataclass(frozen=True)
class _PostEntry:
    name: str
    priority: int
    hook: PostToolUseHook


class HookRegistry:
    """Module-level singleton that holds Pre/PostToolUse hooks.

    Hooks register at startup. ``get_pre_hooks()`` / ``get_post_hooks()``
    return them sorted by priority ascending (lower runs first), then by
    registration order for ties.

    Empty registry is the default — AgentRunner with no hooks registered
    behaves exactly like the pre-hook-system implementation. This is the
    regression contract the M1.A test suite enforces.
    """

    def __init__(self) -> None:
        self._pre: list[_PreEntry] = []
        self._post: list[_PostEntry] = []

    def register_pre(
        self,
        hook: PreToolUseHook,
        *,
        name: str,
        priority: int = 50,
    ) -> None:
        self._pre.append(_PreEntry(name=name, priority=priority, hook=hook))

    def register_post(
        self,
        hook: PostToolUseHook,
        *,
        name: str,
        priority: int = 50,
    ) -> None:
        self._post.append(_PostEntry(name=name, priority=priority, hook=hook))

    def get_pre_hooks(self) -> list[_PreEntry]:
        # Stable sort preserves registration order within a priority band.
        return sorted(self._pre, key=lambda e: e.priority)

    def get_post_hooks(self) -> list[_PostEntry]:
        return sorted(self._post, key=lambda e: e.priority)

    def clear(self) -> None:
        """Test helper — resets the registry between unit tests."""
        self._pre.clear()
        self._post.clear()


# Module-level default registry. Production code uses this; tests can
# instantiate fresh ``HookRegistry()`` to avoid leaking state.
default_registry = HookRegistry()


__all__ = [
    "ApprovalRequest",
    "HookContext",
    "HookDecision",
    "HookRegistry",
    "HookResult",
    "PostToolUseHook",
    "PreToolUseHook",
    "default_registry",
]
