"""Tests for RateLimitHook + its wiring resolution (Phase 4.5 W2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.infra.hooks import HookContext
from app.services.infra.hooks.rate_limit import RateLimitHook


def _ctx(
    *,
    agent_id: str = "00000000-0000-0000-0000-000000000002",
    agent_slug: str = "script_ai",
    delegation_chain: tuple[str, ...] = (),
) -> HookContext:
    return HookContext(
        run_id="0",
        agent_id=UUID(agent_id),
        agent_slug=agent_slug,
        user_id=UUID("00000000-0000-0000-0000-000000000003"),
        session_id=None,
        tool_name="Skill",
        tool_args={"skill": "script-outline"},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
        delegation_chain=delegation_chain,
    )


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.fail = fail

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()

    async def fake_get(_loop_unused: Any = None):
        return redis

    monkeypatch.setattr("app.core.redis.get_async_redis", lambda: fake_get())
    return redis


@pytest.mark.asyncio
async def test_under_limit_continues_and_sets_ttl(fake_redis: FakeRedis) -> None:
    hook = RateLimitHook(limit_per_min=3)
    for _ in range(3):
        assert (await hook(_ctx())).decision == "continue"
    # First INCR of the window arms the TTL exactly once.
    assert list(fake_redis.expires.values()) == [120]


@pytest.mark.asyncio
async def test_over_limit_aborts_with_reason(fake_redis: FakeRedis) -> None:
    hook = RateLimitHook(limit_per_min=2)
    await hook(_ctx())
    await hook(_ctx())
    result = await hook(_ctx())
    assert result.decision == "abort"
    assert "2/min" in result.abort_reason
    assert "script_ai" in result.abort_reason


@pytest.mark.asyncio
async def test_key_scopes_user_and_tree_root(fake_redis: FakeRedis) -> None:
    hook = RateLimitHook(limit_per_min=1)
    # Un-delegated turn: chain root falls back to this agent's slug.
    await hook(_ctx(delegation_chain=("script_ai",)))
    key = next(iter(fake_redis.counts))
    assert "00000000-0000-0000-0000-000000000003" in key  # user
    assert "script_ai" in key  # delegation-tree root slug


@pytest.mark.asyncio
async def test_delegation_subtree_shares_one_bucket(fake_redis: FakeRedis) -> None:
    """Two agents in the SAME delegation tree (different agent_id, same chain
    root) share one bucket — a delegating agent can't multiply the cap."""
    hook = RateLimitHook(limit_per_min=2)
    root = _ctx(
        agent_id="00000000-0000-0000-0000-0000000000aa",
        agent_slug="orchestrator",
        delegation_chain=("orchestrator",),
    )
    sub = _ctx(
        agent_id="00000000-0000-0000-0000-0000000000bb",
        agent_slug="worker",
        delegation_chain=("orchestrator", "worker"),
    )
    assert (await hook(root)).decision == "continue"
    assert (await hook(sub)).decision == "continue"
    # Third call anywhere in the tree trips the shared bucket.
    assert (await hook(sub)).decision == "abort"
    assert len(fake_redis.counts) == 1  # one shared key, not per-agent


@pytest.mark.asyncio
async def test_separate_trees_get_separate_buckets(fake_redis: FakeRedis) -> None:
    """Different delegation-tree roots get independent buckets."""
    hook = RateLimitHook(limit_per_min=1)
    a = _ctx(agent_slug="alpha", delegation_chain=("alpha",))
    b = _ctx(agent_slug="beta", delegation_chain=("beta",))
    assert (await hook(a)).decision == "continue"
    assert (await hook(b)).decision == "continue"  # different tree, own bucket
    assert len(fake_redis.counts) == 2


@pytest.mark.asyncio
async def test_redis_down_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis(fail=True)

    async def fake_get():
        return redis

    monkeypatch.setattr("app.core.redis.get_async_redis", lambda: fake_get())
    hook = RateLimitHook(limit_per_min=1)
    assert (await hook(_ctx())).decision == "continue"
    assert (await hook(_ctx())).decision == "continue"


@pytest.mark.asyncio
async def test_zero_limit_is_noop(fake_redis: FakeRedis) -> None:
    hook = RateLimitHook(limit_per_min=0)
    assert (await hook(_ctx())).decision == "continue"
    assert fake_redis.counts == {}


# ============================================================
# Wiring resolution
# ============================================================


def test_resolution_profile_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.ai.chat.ai_library_chat_wiring import _resolve_tool_rate_limit

    monkeypatch.setenv("AGENT_TOOL_CALLS_PER_MIN", "30")
    assert _resolve_tool_rate_limit({"rate_limit_tool_calls_per_min": 5}) == 5
    # Explicit profile 0 disables even with an env default.
    assert _resolve_tool_rate_limit({"rate_limit_tool_calls_per_min": 0}) == 0


def test_resolution_env_fallback_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat.ai_library_chat_wiring import _resolve_tool_rate_limit

    monkeypatch.setenv("AGENT_TOOL_CALLS_PER_MIN", "30")
    assert _resolve_tool_rate_limit({}) == 30
    monkeypatch.delenv("AGENT_TOOL_CALLS_PER_MIN", raising=False)
    assert _resolve_tool_rate_limit({}) == 0
    monkeypatch.setenv("AGENT_TOOL_CALLS_PER_MIN", "not-a-number")
    assert _resolve_tool_rate_limit({}) == 0
    # Malformed profile value falls through to env/default.
    monkeypatch.setenv("AGENT_TOOL_CALLS_PER_MIN", "12")
    assert _resolve_tool_rate_limit({"rate_limit_tool_calls_per_min": "lots"}) == 12
