"""Bridge adapter — wrap existing services/hooks/* into the new Hook protocol.

Wave F (F9). The codebase already has a Hook framework at
``app.services.hooks`` (BudgetGuard, CostAuditor, MemoryHarvester) with
its own HookContext/HookResult shape. Wave 5c (C3) added a new uniform
protocol at ``agent_framework.hooks_protocol``.

Rather than rewrite the existing 3 hooks, this bridge wraps them as the
new Protocol so HookRegistry can manage them alongside new hooks. Lets
us:
  - Migrate gradually (new hooks use new protocol; existing keep working)
  - Run both kinds through one registry + dispatch
  - Eventually deprecate the old shape once all callers migrate

The bridge translates events:
  HookEvent.PRE_TOOL_USE   → existing PreToolUse hooks
  HookEvent.POST_TOOL_USE  → existing PostToolUse hooks
Other HookEvents are no-op for legacy hooks (they don't subscribe).

Decision mapping:
  legacy 'continue'        → HookResult.continue_()
  legacy 'modify'          → HookResult.continue_(patch={...})
  legacy 'abort'           → HookResult.abort(...)
  legacy 'await_approval'  → HookResult.abort(note='await_approval')
                             (new protocol has no await state yet)
"""
from __future__ import annotations

from typing import Any

from app.agent_framework.hooks_protocol import (
    HookContext as NewHookContext,
)
from app.agent_framework.hooks_protocol import (
    HookEvent,
    HookResult as NewHookResult,
)


class LegacyPreToolUseHook:
    """Adapter: wraps a legacy PreToolUse hook (services/hooks/*) into
    the new Protocol with events={PRE_TOOL_USE}."""

    def __init__(self, legacy_hook: Any, *, name_prefix: str = "legacy_pre"):
        self._legacy = legacy_hook
        cls_name = type(legacy_hook).__name__.lower()
        self.name = f"{name_prefix}_{cls_name}"

    @property
    def events(self) -> frozenset[HookEvent]:
        return frozenset({HookEvent.PRE_TOOL_USE})

    async def __call__(self, ctx: NewHookContext) -> NewHookResult:
        return await _invoke_legacy(self._legacy, ctx)


class LegacyPostToolUseHook:
    """Adapter for legacy PostToolUse hooks."""

    def __init__(self, legacy_hook: Any, *, name_prefix: str = "legacy_post"):
        self._legacy = legacy_hook
        cls_name = type(legacy_hook).__name__.lower()
        self.name = f"{name_prefix}_{cls_name}"

    @property
    def events(self) -> frozenset[HookEvent]:
        return frozenset({HookEvent.POST_TOOL_USE})

    async def __call__(self, ctx: NewHookContext) -> NewHookResult:
        return await _invoke_legacy(self._legacy, ctx)


async def _invoke_legacy(legacy_hook: Any, ctx: NewHookContext) -> NewHookResult:
    """Translate new ctx → legacy ctx, invoke, translate result back."""
    from app.services.hooks import HookContext as LegacyCtx

    legacy_ctx = LegacyCtx(
        run_id=ctx.run_id,
        agent_id=ctx.agent_id,
        agent_slug=ctx.payload.get("agent_slug", ""),
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        tool_name=ctx.payload.get("tool_name", ""),
        tool_args=ctx.payload.get("tool_args", {}),
        accumulated_prompt_tokens=ctx.payload.get(
            "accumulated_prompt_tokens", 0
        ),
        accumulated_completion_tokens=ctx.payload.get(
            "accumulated_completion_tokens", 0
        ),
        accumulated_cost_cents=ctx.payload.get("accumulated_cost_cents", 0.0),
        iteration=ctx.turn_idx,
    )

    try:
        legacy_result = await legacy_hook(legacy_ctx)
    except Exception as exc:  # noqa: BLE001
        # Match new protocol's "broken hooks swallowed" contract
        return NewHookResult.continue_(note=f"legacy hook raised {exc!r}")

    # Side-effect support: legacy hooks may return a callable to fire.
    side = getattr(legacy_result, "side_effect", None)
    if callable(side):
        try:
            side()
        except Exception:
            pass  # swallow — telemetry side effects must not abort the run

    decision = getattr(legacy_result, "decision", "continue")
    if decision == "abort":
        return NewHookResult.abort(
            getattr(legacy_result, "reason", None) or "legacy hook aborted"
        )
    if decision == "await_approval":
        return NewHookResult.abort(note="legacy hook requested await_approval")
    if decision == "modify":
        modified_args = getattr(legacy_result, "modified_args", None)
        return NewHookResult.continue_(
            patch={"tool_args": modified_args} if modified_args else None
        )
    return NewHookResult.continue_()


def wrap_legacy_pre(legacy_hook: Any) -> LegacyPreToolUseHook:
    return LegacyPreToolUseHook(legacy_hook)


def wrap_legacy_post(legacy_hook: Any) -> LegacyPostToolUseHook:
    return LegacyPostToolUseHook(legacy_hook)


__all__ = [
    "LegacyPostToolUseHook",
    "LegacyPreToolUseHook",
    "wrap_legacy_post",
    "wrap_legacy_pre",
]
