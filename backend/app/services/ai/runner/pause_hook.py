"""Seam A hook: target-level pause (harness phase 2a §2).

A user pauses an ISSUE (``POST /issues/{id}/pause``), not a run. The endpoint
stamps ``issues.paused_at`` and flips ``agent_runs.pause_requested`` on the
root run in flight; this hook is where the run notices, at its next step
boundary, and stops with ``stop_reason="paused"`` (→ ``turn_end{reason:
paused}`` → the issue workflow returns without routing status, so the issue
stays ``in_progress`` with ``paused_at`` as the only truth).

Only the ROOT run polls: a sub-run (delegated agent, pipeline step) belongs
to its parent's step and ends when the parent stops — polling it too would
double the SELECTs and let a sub-run park itself half-way through a tool.
"""

from __future__ import annotations

from app.services.ai.runner.step_hooks import StepContext, StepDecision


class PauseHook:
    name = "pause"

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        if ctx.parent_run_id is not None:
            return StepDecision.CONTINUE
        rec = ctx.recorder
        if rec is None or not hasattr(rec, "check_paused"):
            return StepDecision.CONTINUE
        if await rec.check_paused():
            return ctx.stop("paused")
        return StepDecision.CONTINUE


__all__ = ["PauseHook"]
