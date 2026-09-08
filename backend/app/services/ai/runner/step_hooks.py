"""Step-boundary hook chain (harness p4, seam A).

One explicit, ordered place for every cross-cutting behaviour that must run
between two model calls: heartbeat, cooperative cancel, and — in later
tasks — pause, inbox claim, budget gate, question injection. The runner
calls ``await self.step_hooks.run(ctx)`` exactly once per step in each of
its two paths; a source guard pins that count.

Why a chain and not more ``if`` blocks in the loop: the runner's two inner
functions already carry ~25 exits between them. Every new behaviour added
inline is a second copy (one per path) and a new place for the two paths
to drift. Here a behaviour is one small class registered once in
``build_agent_runner_stack``; the order is visible in one list.

Contract (mirrors dsh ``agent/pre-step`` and the repo's LLM middleware
chain):

* hooks run in registration order;
* the first ``STOP`` wins — later hooks do not run for that step;
* a hook that raises is logged and skipped (a bad subscriber never
  breaks the core lifecycle — CLAUDE.md "分发器要容纳回调异常");
* a hook may append user-role messages via ``ctx.inject(...)``; the runner
  extends its message list with ``ctx.injected`` after the chain returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, Sequence

from loguru import logger


class StepDecision(str, Enum):
    CONTINUE = "continue"
    STOP = "stop"


@dataclass
class StepContext:
    """What a hook may look at and what it may change for this step."""

    turn: int
    step: int
    recorder: Any = None
    composed: Any = None
    messages: list[dict] = field(default_factory=list)
    run_id: Optional[int] = None
    parent_run_id: Optional[str] = None
    stop_reason: Optional[str] = None
    injected: list[dict] = field(default_factory=list)
    is_stream: bool = False

    def inject(self, message: dict) -> None:
        self.injected.append(message)

    def stop(self, reason: str) -> StepDecision:
        # Deferred import: turn_end imports events; keep this module leaf-light.
        from app.services.ai.runner.turn_end import STOP_REASON_TO_TURN_END

        if reason not in STOP_REASON_TO_TURN_END:
            raise ValueError(
                f"unknown stop reason {reason!r}; add it to "
                "turn_end.STOP_REASON_TO_TURN_END"
            )
        self.stop_reason = reason
        return StepDecision.STOP


class StepBoundaryHook(Protocol):
    name: str

    async def before_llm_call(self, ctx: StepContext) -> StepDecision: ...


class HeartbeatHook:
    """Refresh ``heartbeat_at`` so the sweeper never marks a live multi-step
    run ``heartbeat_lost``. Was inline in both paths."""

    name = "heartbeat"

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        if ctx.recorder is not None and hasattr(ctx.recorder, "heartbeat"):
            await ctx.recorder.heartbeat()
        return StepDecision.CONTINUE


class CancelHook:
    """Cooperative cancel: ``cancel_requested`` flipped in the DB stops the
    turn before the next model call. Was inline in both paths."""

    name = "cancel"

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        rec = ctx.recorder
        if rec is not None and hasattr(rec, "check_cancelled"):
            if await rec.check_cancelled():
                return ctx.stop("cancelled")
        return StepDecision.CONTINUE


class StepHookChain:
    def __init__(self, hooks: Sequence[StepBoundaryHook]):
        self._hooks: tuple[StepBoundaryHook, ...] = tuple(hooks)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(h.name for h in self._hooks)

    async def run(self, ctx: StepContext) -> StepDecision:
        for hook in self._hooks:
            try:
                decision = await hook.before_llm_call(ctx)
            except Exception as exc:  # noqa: BLE001 — contain, log, continue
                logger.warning(
                    "[step_hooks] hook {} raised and was skipped: {!r}", hook.name, exc
                )
                continue
            if decision is StepDecision.STOP:
                return StepDecision.STOP
        return StepDecision.CONTINUE


def default_step_hooks() -> StepHookChain:
    """The chain every runner gets when the wiring passes none — exactly the
    behaviour the two inline blocks had (heartbeat, then cancel)."""
    return StepHookChain([HeartbeatHook(), CancelHook()])


__all__ = [
    "CancelHook",
    "HeartbeatHook",
    "StepBoundaryHook",
    "StepContext",
    "StepDecision",
    "StepHookChain",
    "default_step_hooks",
]
