"""C3 — Hook protocol + registry."""

from __future__ import annotations

import pytest

from app.agent_framework.hooks_protocol import (
    DuplicateHookError,
    Hook,
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
    HookResult,
)

# ─── Test fixtures ────────────────────────────────────────────────────


class _PreToolUseHook:
    """Test hook subscribed only to PRE_TOOL_USE."""

    name = "test_pre"
    events = frozenset({HookEvent.PRE_TOOL_USE})

    def __init__(self):
        self.called = 0
        self.last_ctx: HookContext | None = None

    async def __call__(self, ctx: HookContext) -> HookResult:
        self.called += 1
        self.last_ctx = ctx
        return HookResult.continue_()


class _AbortingHook:
    name = "abort_me"
    events = frozenset({HookEvent.PRE_LLM_CALL})

    async def __call__(self, ctx: HookContext) -> HookResult:
        return HookResult.abort("budget exceeded")


class _MutatingHook:
    name = "mutator"
    events = frozenset({HookEvent.TURN_START})

    async def __call__(self, ctx: HookContext) -> HookResult:
        return HookResult.continue_(patch={"injected_key": "v1"})


class _BrokenHook:
    name = "broken"
    events = frozenset({HookEvent.TURN_START})

    async def __call__(self, ctx: HookContext) -> HookResult:
        raise RuntimeError("boom")


# ─── Protocol compliance ──────────────────────────────────────────────


@pytest.mark.unit
def test_protocol_runtime_check_passes():
    h = _PreToolUseHook()
    assert isinstance(h, Hook)


# ─── Registry basics ──────────────────────────────────────────────────


@pytest.mark.unit
def test_register_and_lookup():
    reg = HookRegistry()
    h = _PreToolUseHook()
    reg.register(h)
    assert reg.get("test_pre") is h
    assert "test_pre" in reg
    assert len(reg) == 1


@pytest.mark.unit
def test_duplicate_registration_rejected():
    reg = HookRegistry()
    reg.register(_PreToolUseHook())
    with pytest.raises(DuplicateHookError):
        reg.register(_PreToolUseHook())


@pytest.mark.unit
def test_unregister_returns_bool():
    reg = HookRegistry()
    reg.register(_PreToolUseHook())
    assert reg.unregister("test_pre") is True
    assert reg.unregister("test_pre") is False


@pytest.mark.unit
def test_names_sorted():
    reg = HookRegistry()
    reg.register(_AbortingHook())
    reg.register(_PreToolUseHook())
    assert reg.names() == ["abort_me", "test_pre"]


# ─── Event dispatch ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_only_invokes_subscribed_hooks():
    reg = HookRegistry()
    pre = _PreToolUseHook()
    reg.register(pre)
    # Fire a different event — should NOT call pre
    await reg.fire(HookEvent.TURN_START, HookContext(event=HookEvent.TURN_START))
    assert pre.called == 0
    # Now fire the matching event
    await reg.fire(HookEvent.PRE_TOOL_USE, HookContext(event=HookEvent.PRE_TOOL_USE))
    assert pre.called == 1


@pytest.mark.asyncio
async def test_abort_short_circuits():
    reg = HookRegistry()
    aborter = _AbortingHook()
    pre = _PreToolUseHook()
    reg.register(aborter)
    reg.register(pre)
    # Fire PRE_LLM_CALL — aborter triggers abort, pre is not called (different event anyway)
    res = await reg.fire(
        HookEvent.PRE_LLM_CALL, HookContext(event=HookEvent.PRE_LLM_CALL)
    )
    assert res.decision == HookDecision.ABORT_RUN
    assert "budget" in (res.note or "")


@pytest.mark.asyncio
async def test_mutating_hook_patches_payload():
    reg = HookRegistry()
    reg.register(_MutatingHook())
    res = await reg.fire(
        HookEvent.TURN_START,
        HookContext(event=HookEvent.TURN_START, payload={"existing": "x"}),
    )
    assert res.decision == HookDecision.CONTINUE
    assert res.payload_patch is not None
    assert res.payload_patch.get("injected_key") == "v1"
    assert res.payload_patch.get("existing") == "x"


@pytest.mark.asyncio
async def test_broken_hook_does_not_crash_run():
    """Crashing hook is logged + skipped — never propagates exception."""
    reg = HookRegistry()
    reg.register(_BrokenHook())
    other = _MutatingHook()  # subscribes to same event
    reg.register(other)
    # Should not raise
    res = await reg.fire(HookEvent.TURN_START, HookContext(event=HookEvent.TURN_START))
    # The non-broken hook still ran
    assert res.payload_patch is not None
    assert res.payload_patch.get("injected_key") == "v1"


@pytest.mark.asyncio
async def test_fire_with_no_hooks_returns_continue():
    reg = HookRegistry()
    res = await reg.fire(HookEvent.TURN_START, HookContext(event=HookEvent.TURN_START))
    assert res.decision == HookDecision.CONTINUE


@pytest.mark.unit
def test_hook_result_classmethods():
    cont = HookResult.continue_(note="hi")
    assert cont.decision == HookDecision.CONTINUE
    assert cont.note == "hi"

    ab = HookResult.abort("nope")
    assert ab.decision == HookDecision.ABORT_RUN
    assert ab.note == "nope"


@pytest.mark.unit
def test_empty_hook_name_rejected():
    class _Bad:
        name = ""
        events = frozenset()

        async def __call__(self, ctx):
            return HookResult.continue_()

    reg = HookRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register(_Bad())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_payload_defensive_copy():
    """Caller's HookContext.payload mutations don't affect later hooks."""
    reg = HookRegistry()
    reg.register(_MutatingHook())
    initial = {"k": "v"}
    ctx = HookContext(event=HookEvent.TURN_START, payload=initial)
    await reg.fire(HookEvent.TURN_START, ctx)
    # Caller's original dict is untouched
    assert initial == {"k": "v"}
