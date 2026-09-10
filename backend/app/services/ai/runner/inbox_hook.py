"""Seam A hook: claim the run's inbox at every step boundary (spec §1-③).

Only a ROOT run (``parent_run_id IS NULL``) claims — a sub-agent must not
swallow a steer meant for the conversation it was spawned from. Runs with no
target (backfills, probes) claim nothing. Each claimed item is injected as a
user message and recorded as ``inbox_claimed`` with the step coordinates.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Sequence

from app.repositories.agent_run_inbox_repository import Target
from app.services.ai.runner import inbox as inbox_mod
from app.services.ai.runner.events import emit
from app.services.ai.runner.step_hooks import StepContext, StepDecision

Claim = Callable[
    [Sequence[Target], int, int, int], Awaitable[list[inbox_mod.InboxItem]]
]
Resolve = Callable[..., Awaitable[list[Target]]]


class InboxClaimHook:
    name = "inbox.claim"

    def __init__(
        self, *, claim: Optional[Claim] = None, resolve: Optional[Resolve] = None
    ):
        self._claim = claim or inbox_mod.claim_for_step
        self._resolve = resolve or inbox_mod.resolve_targets
        self._targets_by_run: dict[int, list[Target]] = {}

    async def _targets(self, run_id: int, recorder: Any) -> list[Target]:
        if run_id not in self._targets_by_run:
            self._targets_by_run[run_id] = await self._resolve(
                issue_id=getattr(recorder, "issue_id", None),
                conversation_id=getattr(recorder, "conversation_id", None),
            )
        return self._targets_by_run[run_id]

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        if ctx.parent_run_id is not None:
            return StepDecision.CONTINUE
        recorder = ctx.recorder
        run_id = getattr(recorder, "run_id", None)
        if recorder is None or run_id is None:
            return StepDecision.CONTINUE
        targets = await self._targets(int(run_id), recorder)
        if not targets:
            return StepDecision.CONTINUE
        for item in await self._claim(targets, int(run_id), ctx.turn, ctx.step):
            ctx.inject(
                {"role": "user", "content": inbox_mod.render_inbox_message(item)}
            )
            await emit(
                recorder,
                "inbox_claimed",
                # ids are Snowflake BIGINT — as strings so the payload survives JS
                {
                    "inbox_id": str(item.id),
                    "kind": item.kind,
                    "turn": ctx.turn,
                    "step": ctx.step,
                    # WHAT was claimed, bounded. Without it the trajectory fold
                    # defaulted every field, and ``status`` defaults to
                    # ``completed`` — a failed sub-agent rendered as ✓ Done
                    # (Task 7b defect G). ``claimed_event_content`` owns the
                    # shape and the truncation; this is the transcript's copy,
                    # not the model's (that is ``render_inbox_message`` above).
                    "content": inbox_mod.claimed_event_content(item),
                },
                turn=ctx.turn,
                step=ctx.step,
            )
        return StepDecision.CONTINUE


__all__ = ["InboxClaimHook"]
