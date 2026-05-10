"""Generic hook protocol — replaces the current per-feature wiring.

Wave 5c (C3). Today services/hooks/ holds three concrete classes
(BudgetGuard, CostAuditor, MemoryHarvester) each with their own ad-hoc
integration call site. Adding a new hook means hunting down where to
plug it in. Removing one means cleaning up scattered references.

This module defines a uniform Protocol + Registry so:
  - hooks register themselves declaratively (events they care about)
  - the runner fires events at well-known points (PRE_TOOL_USE etc.)
  - the registry dispatches; hooks return HookResult.continue_ /
    .abort_run / .mutate_payload(new)
  - new hooks just implement Hook + register; no runner changes

The existing concrete hooks (BudgetGuard, CostAuditor, MemoryHarvester)
keep working — they're not migrated by this commit. New hooks should
use the new protocol; migration of existing ones is deferred to keep
this change reviewable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable


class HookEvent(str, Enum):
    """Events the runner fires. Hook subscribes to one or more."""

    TURN_START = "turn_start"
    PRE_LLM_CALL = "pre_llm_call"
    POST_LLM_CALL = "post_llm_call"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    TURN_END = "turn_end"
    RUN_STOP = "run_stop"


@dataclass
class HookContext:
    """Per-event payload shared with all hooks. Mutating ``payload`` is
    how hooks influence subsequent processing (e.g. PRE_LLM_CALL hook
    appends a system message)."""

    event: HookEvent
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    turn_idx: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


# Decisions a hook can return.
class HookDecision(str, Enum):
    CONTINUE = "continue"
    ABORT_RUN = "abort_run"


@dataclass(frozen=True)
class HookResult:
    """What a hook returns. ``decision=ABORT_RUN`` short-circuits the
    runner; ``payload_patch`` is merged into ctx.payload before the
    next hook in the chain."""

    decision: HookDecision = HookDecision.CONTINUE
    payload_patch: Optional[dict[str, Any]] = None
    note: Optional[str] = None  # for audit trail

    @classmethod
    def continue_(
        cls, *, patch: Optional[dict[str, Any]] = None, note: Optional[str] = None
    ) -> "HookResult":
        return cls(decision=HookDecision.CONTINUE, payload_patch=patch, note=note)

    @classmethod
    def abort(cls, note: str) -> "HookResult":
        return cls(decision=HookDecision.ABORT_RUN, note=note)


HookCallable = Callable[[HookContext], Awaitable[HookResult]]


@runtime_checkable
class Hook(Protocol):
    """Hook contract.

    Implementations expose ``name``, ``events`` (which events to subscribe
    to), and ``__call__`` async. Sync hooks aren't supported — the runner
    is async, and forcing async is the simpler contract."""

    name: str

    @property
    def events(self) -> frozenset[HookEvent]: ...

    async def __call__(self, ctx: HookContext) -> HookResult: ...


class DuplicateHookError(ValueError):
    """A hook with this name is already registered."""


class HookRegistry:
    """Per-process registry of hooks. Run-level dispatch.

    Not thread-safe; one registry per asyncio event loop. Typical
    lifecycle: created at FastAPI lifespan startup, populated, then
    read-only.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, Hook] = {}

    def register(self, hook: Hook) -> None:
        if not hook.name:
            raise ValueError("hook.name must be non-empty")
        if hook.name in self._hooks:
            raise DuplicateHookError(f"hook '{hook.name}' is already registered")
        self._hooks[hook.name] = hook

    def unregister(self, name: str) -> bool:
        return self._hooks.pop(name, None) is not None

    def get(self, name: str) -> Optional[Hook]:
        return self._hooks.get(name)

    def names(self) -> list[str]:
        return sorted(self._hooks)

    async def fire(self, event: HookEvent, ctx: HookContext) -> HookResult:
        """Dispatch ``event`` to all subscribers. Short-circuits on
        first ABORT_RUN. Returns the final aggregate result.

        Order is registration order (insertion order of dict).
        """
        ctx = HookContext(
            event=event,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            turn_idx=ctx.turn_idx,
            payload=dict(ctx.payload),  # defensive copy
        )
        last_note: Optional[str] = None
        for hook in self._hooks.values():
            if event not in hook.events:
                continue
            try:
                res = await hook(ctx)
            except Exception as exc:  # noqa: BLE001
                # A buggy hook must not crash the run — log + skip.
                last_note = f"hook '{hook.name}' raised {type(exc).__name__}: {exc}"
                continue
            if res.payload_patch:
                ctx.payload.update(res.payload_patch)
            if res.note:
                last_note = res.note
            if res.decision == HookDecision.ABORT_RUN:
                from app.agent_framework._metrics_helper import inc_metric

                inc_metric("hook_aborted_run")
                return HookResult.abort(
                    res.note or f"hook '{hook.name}' aborted the run"
                )
        return HookResult.continue_(patch=ctx.payload, note=last_note)

    def __len__(self) -> int:
        return len(self._hooks)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._hooks


__all__ = [
    "DuplicateHookError",
    "Hook",
    "HookCallable",
    "HookContext",
    "HookDecision",
    "HookEvent",
    "HookRegistry",
    "HookResult",
]
