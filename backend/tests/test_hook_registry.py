"""Unit tests for HookRegistry — registration, priority ordering, isolation."""

from __future__ import annotations

import pytest

from app.services.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
)


async def _noop_pre(ctx: HookContext) -> HookResult:
    return HookResult(decision="continue")


async def _noop_post(ctx: HookContext, result: dict) -> HookResult:
    return HookResult(decision="continue")


@pytest.mark.unit
def test_empty_registry_returns_empty_lists():
    reg = HookRegistry()
    assert reg.get_pre_hooks() == []
    assert reg.get_post_hooks() == []


@pytest.mark.unit
def test_register_pre_priority_sort():
    reg = HookRegistry()
    reg.register_pre(_noop_pre, name="late", priority=80)
    reg.register_pre(_noop_pre, name="early", priority=10)
    reg.register_pre(_noop_pre, name="mid", priority=50)

    names = [e.name for e in reg.get_pre_hooks()]
    assert names == ["early", "mid", "late"]


@pytest.mark.unit
def test_register_pre_same_priority_preserves_registration_order():
    reg = HookRegistry()
    reg.register_pre(_noop_pre, name="first", priority=50)
    reg.register_pre(_noop_pre, name="second", priority=50)
    reg.register_pre(_noop_pre, name="third", priority=50)

    names = [e.name for e in reg.get_pre_hooks()]
    assert names == ["first", "second", "third"]


@pytest.mark.unit
def test_register_post_priority_sort():
    reg = HookRegistry()
    reg.register_post(_noop_post, name="late", priority=99)
    reg.register_post(_noop_post, name="early", priority=1)

    names = [e.name for e in reg.get_post_hooks()]
    assert names == ["early", "late"]


@pytest.mark.unit
def test_clear_resets_both_chains():
    reg = HookRegistry()
    reg.register_pre(_noop_pre, name="a")
    reg.register_post(_noop_post, name="b")
    assert reg.get_pre_hooks() and reg.get_post_hooks()

    reg.clear()
    assert reg.get_pre_hooks() == []
    assert reg.get_post_hooks() == []


@pytest.mark.unit
def test_default_registry_is_isolated_per_test():
    """Reads `default_registry` for assertion that it is a singleton — but
    test cases must clear it themselves to avoid pollution."""
    from app.services.hooks import default_registry

    default_registry.clear()
    assert default_registry.get_pre_hooks() == []
    default_registry.register_pre(_noop_pre, name="x")
    assert len(default_registry.get_pre_hooks()) == 1
    default_registry.clear()
