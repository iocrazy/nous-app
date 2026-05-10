"""F9 — bridge legacy hooks into new HookRegistry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.agent_framework.hooks_bridge import (
    LegacyPostToolUseHook,
    LegacyPreToolUseHook,
    wrap_legacy_post,
    wrap_legacy_pre,
)
from app.agent_framework.hooks_protocol import (
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
)
from app.services.infra.hooks import HookContext as LegacyCtx


@dataclass
class _LegacyResult:
    """Stand-in for legacy services/hooks HookResult."""

    decision: str = "continue"
    reason: str | None = None
    modified_args: dict[str, Any] | None = None
    side_effect: Any = None


class _LegacyContinueHook:
    """Plain pass-through legacy hook."""

    async def __call__(self, ctx: LegacyCtx):
        return _LegacyResult(decision="continue")


class _LegacyAbortHook:
    async def __call__(self, ctx: LegacyCtx):
        return _LegacyResult(decision="abort", reason="budget exceeded")


class _LegacyModifyHook:
    async def __call__(self, ctx: LegacyCtx):
        return _LegacyResult(decision="modify", modified_args={"new_arg": "value"})


class _LegacyAwaitApprovalHook:
    async def __call__(self, ctx: LegacyCtx):
        return _LegacyResult(decision="await_approval")


class _LegacyBrokenHook:
    async def __call__(self, ctx: LegacyCtx):
        raise RuntimeError("legacy hook crashed")


class _LegacySideEffectHook:
    """Legacy hook with side-effect callable."""

    def __init__(self):
        self.side_called = 0

    async def __call__(self, ctx: LegacyCtx):
        def _side():
            self.side_called += 1

        return _LegacyResult(decision="continue", side_effect=_side)


# ─── Adapter shape ────────────────────────────────────────────────────


@pytest.mark.unit
def test_wrap_pre_returns_pre_subscriber():
    h = wrap_legacy_pre(_LegacyContinueHook())
    assert isinstance(h, LegacyPreToolUseHook)
    assert h.events == frozenset({HookEvent.PRE_TOOL_USE})


@pytest.mark.unit
def test_wrap_post_returns_post_subscriber():
    h = wrap_legacy_post(_LegacyContinueHook())
    assert isinstance(h, LegacyPostToolUseHook)
    assert h.events == frozenset({HookEvent.POST_TOOL_USE})


@pytest.mark.unit
def test_wrapper_name_includes_legacy_class():
    h = wrap_legacy_pre(_LegacyContinueHook())
    assert "legacycontinuehook" in h.name


# ─── Decision translation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_continue_passes_through():
    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_LegacyContinueHook()))
    res = await reg.fire(
        HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE)
    )
    assert res.decision == HookDecision.CONTINUE


@pytest.mark.asyncio
async def test_abort_translates():
    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_LegacyAbortHook()))
    res = await reg.fire(
        HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE)
    )
    assert res.decision == HookDecision.ABORT_RUN
    assert "budget" in (res.note or "")


@pytest.mark.asyncio
async def test_modify_translates_to_payload_patch():
    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_LegacyModifyHook()))
    res = await reg.fire(
        HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE)
    )
    assert res.decision == HookDecision.CONTINUE
    assert res.payload_patch is not None
    assert res.payload_patch.get("tool_args") == {"new_arg": "value"}


@pytest.mark.asyncio
async def test_await_approval_translates_to_abort():
    """New protocol has no await state yet — map to abort to avoid silent loss."""
    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_LegacyAwaitApprovalHook()))
    res = await reg.fire(
        HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE)
    )
    assert res.decision == HookDecision.ABORT_RUN
    assert "await_approval" in (res.note or "")


@pytest.mark.asyncio
async def test_broken_legacy_hook_does_not_crash():
    """Legacy hook crash → translated to continue (matches HookRegistry's
    'broken hooks swallowed' contract)."""
    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_LegacyBrokenHook()))
    res = await reg.fire(
        HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE)
    )
    assert res.decision == HookDecision.CONTINUE


@pytest.mark.asyncio
async def test_side_effect_invoked():
    reg = HookRegistry()
    legacy = _LegacySideEffectHook()
    reg.register(wrap_legacy_pre(legacy))
    await reg.fire(HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE))
    assert legacy.side_called == 1


@pytest.mark.asyncio
async def test_pre_hook_not_called_for_post_event():
    """A wrapped PRE hook must NOT fire on POST_TOOL_USE."""
    reg = HookRegistry()
    captured = {"n": 0}

    class _Counter:
        async def __call__(self, ctx):
            captured["n"] += 1
            return _LegacyResult(decision="continue")

    reg.register(wrap_legacy_pre(_Counter()))
    await reg.fire(HookEvent.POST_TOOL_USE, HookContext(event=HookEvent.POST_TOOL_USE))
    assert captured["n"] == 0


@pytest.mark.asyncio
async def test_payload_translation_preserves_tool_name():
    """Payload from new ctx → legacy ctx.tool_name."""
    captured = {}

    class _Capture:
        async def __call__(self, ctx):
            captured["tool_name"] = ctx.tool_name
            captured["iteration"] = ctx.iteration
            return _LegacyResult(decision="continue")

    reg = HookRegistry()
    reg.register(wrap_legacy_pre(_Capture()))
    await reg.fire(
        HookEvent.PRE_TOOL_USE,
        HookContext(
            event=HookEvent.PRE_TOOL_USE,
            turn_idx=5,
            payload={"tool_name": "Skill", "tool_args": {"x": 1}},
        ),
    )
    assert captured["tool_name"] == "Skill"
    assert captured["iteration"] == 5
