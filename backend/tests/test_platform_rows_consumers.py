"""Every backend decision over platform models reads the platform provider
view (spec 2026-09-25 P4 "全部统一").

One pair per consumer: a model the user switched off / governance hid / the
engine stopped listing is NOT chosen; a normal one IS. The catalog, the
governance switch and the engine are stubbed at the view's own seams
(``tests.services.ai.test_platform_provider.Env``), so these tests run the
real ``platform_rows`` computation, not a stub of it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai.platform_provider import PlatformModelNotAvailableError
from app.services.media.parsers.video_providers import db_registry
from tests.services.ai.test_platform_provider import (
    Env,
    catalog_row,
    engine_row,
    listed,
)

pytestmark = pytest.mark.asyncio

USER = "00000000-0000-0000-0000-000000000077"


@pytest.fixture
def env(monkeypatch):
    e = Env(monkeypatch)
    e.stored = {}
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(side_effect=lambda _u: e.stored),
    )
    monkeypatch.setattr(db_registry, "byok_image_rows", AsyncMock(return_value=[]))

    def _by_name(name):
        return next((r for r in e.rows if r["name"] == name), None)

    e.repo.get_by_name = AsyncMock(side_effect=_by_name)
    e.repo.get_by_actual_model = AsyncMock(return_value=None)
    return e


def _images():
    return [
        catalog_row("nous-seedream-a", type="image", actual_model="seedream-a"),
        catalog_row("nous-seedream-b", type="image", actual_model="seedream-b"),
    ]


# ─── 1. image/video dispatch default pick (db_registry) ─────────────────────


async def test_dispatch_default_pick_takes_the_first_usable_row(env):
    env.rows = _images()
    _, actual = await db_registry.resolve_image_provider(None, user_id=USER)
    assert actual == "seedream-a"
    env.repo.list_enabled_private.assert_awaited_with(USER)


async def test_dispatch_default_pick_skips_a_row_the_user_disabled(env):
    env.rows = _images()
    env.stored = {"disabled_models": ["nous-seedream-a"]}
    _, actual = await db_registry.resolve_image_provider(None, user_id=USER)
    assert actual == "seedream-b"


async def test_dispatch_has_no_row_when_governance_is_off(env):
    env.rows = _images()
    env.governance = False
    with pytest.raises(RuntimeError, match="no image model configured"):
        await db_registry.resolve_image_provider(None, user_id=USER)


async def test_dispatch_rows_drop_a_service_the_engine_no_longer_lists(env):
    env.rows = [
        engine_row("nous-gone", "gone-t2v", type="video"),
        engine_row("nous-here", "here-t2v", type="video"),
    ]
    env.engine_answers = [listed(("here-t2v", True))]
    rows = await db_registry._enabled_rows("video", USER)
    assert [r["name"] for r in rows] == ["nous-here"]
    assert rows[0]["status"] == "ok" and rows[0]["api_key"] == "sk-engine"


async def test_dispatch_refuses_a_disabled_model_named_explicitly(env):
    """Named explicitly, a withheld model is refused BY NAME — never silently
    replaced by the default pick."""
    env.rows = _images()
    env.stored = {"disabled_models": ["nous-seedream-a"]}
    with pytest.raises(PlatformModelNotAvailableError) as info:
        await db_registry.resolve_image_provider("nous-seedream-a", user_id=USER)
    assert info.value.details["reason"] == "user_disabled"


async def test_dispatch_refuses_a_named_video_row_the_view_withheld(env):
    env.rows = [engine_row("nous-gone", "gone-t2v", type="video")]
    env.engine_answers = [listed(("other", True))]
    with pytest.raises(PlatformModelNotAvailableError) as info:
        await db_registry.resolve_video_route("nous-gone", user_id=USER)
    assert info.value.details["reason"] == "not_served"


# ─── 2. canvas implicit default text model ─────────────────────────────────


async def test_canvas_default_text_model_follows_the_users_view(env):
    from app.services.canvas.canvas_run_service import CanvasRunService

    env.rows = [catalog_row("nous-a"), catalog_row("nous-b")]
    svc = CanvasRunService()
    assert await svc._default_text_model(USER) == "nous-a"
    env.stored = {"disabled_models": ["nous-a"]}
    assert await svc._default_text_model(USER) == "nous-b"


async def test_canvas_default_text_model_skips_an_unlisted_engine_row(env):
    from app.services.canvas.canvas_run_service import CanvasRunService

    env.rows = [engine_row("nous-gone", "gone-llm"), catalog_row("nous-b")]
    env.engine_answers = [listed(("other", True))]
    assert await CanvasRunService()._default_text_model(USER) == "nous-b"


# ─── B. resolve_nous_model with the user's gates ───────────────────────────


@pytest.fixture
def nous_allowed(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.is_nous_allowed",
        AsyncMock(return_value=True),
    )


async def test_resolve_nous_model_serves_a_normal_pick(env, nous_allowed):
    from app.services.ai.providers.ai_provider_helpers import resolve_nous_model

    env.rows = [catalog_row("nous-a")]
    hit = await resolve_nous_model("nous-a", "chat", user_id=USER)
    assert hit is not None and hit[2] == "nous-a-upstream"


@pytest.mark.parametrize(
    "stored, owner, reason",
    [
        ({"disabled_models": ["nous-a"]}, None, "user_disabled"),
        ({"enabled": False}, None, "platform_card_disabled"),
        ({}, "someone-else", "owner_scope"),
    ],
)
async def test_resolve_nous_model_refuses_what_the_user_may_not_use(
    env, nous_allowed, stored, owner, reason
):
    from app.services.ai.providers.ai_provider_helpers import resolve_nous_model

    env.rows = [catalog_row("nous-a", owner_user_id=owner)]
    env.stored = stored
    with pytest.raises(PlatformModelNotAvailableError) as info:
        await resolve_nous_model("nous-a", "chat", user_id=USER)
    assert info.value.details == {
        "code": "platform_model_not_available",
        "model": "nous-a",
        "reason": reason,
    }


async def test_resolve_nous_model_without_a_user_is_unchanged(env, nous_allowed):
    """System / admin-chosen models (maintenance, compaction) keep resolving:
    a user's blacklist does not govern models they never picked."""
    from app.services.ai.providers.ai_provider_helpers import resolve_nous_model

    env.rows = [catalog_row("nous-a")]
    env.stored = {"disabled_models": ["nous-a"]}
    assert await resolve_nous_model("nous-a", "maintenance") is not None


async def test_task_config_nous_pick_the_user_disabled_degrades_to_default(
    env, nous_allowed, monkeypatch
):
    """``resolve_task_ai_config`` already degrades a gone ``nous:<model>``
    pick to the module's default agent; a disabled one does the same."""
    from app.services.ai.providers import ai_provider_helpers as h

    env.rows = [catalog_row("nous-a")]
    env.stored = {"disabled_models": ["nous-a"]}
    monkeypatch.setattr(
        h,
        "get_ai_settings",
        AsyncMock(return_value={"task_assignment": {"summarization": "nous:nous-a"}}),
    )
    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.resolve_locked_module_config",
        AsyncMock(return_value=None),
    )
    agents = AsyncMock()
    agents.get_by_slug = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.repositories.agent_repository.get_agent_repository", lambda: agents
    )
    cfg = await h.resolve_task_ai_config(USER, "summarization", "summarize")
    assert cfg.origin != "platform"
    agents.get_by_slug.assert_awaited_with("summarize")


# ─── B. chat fallback pool (build_fallback_llm gate_user_id) ───────────────


async def test_fallback_pool_drops_a_model_the_user_disabled(env, nous_allowed):
    from app.services.ai.llm.fallback_wiring import build_fallback_llm

    env.rows = [catalog_row("nous-a"), catalog_row("nous-b"), catalog_row("nous-c")]
    env.stored = {"disabled_models": ["nous-b"]}
    chain = await build_fallback_llm(
        primary_model="nous-a",
        fallback_models=["nous-b", "nous-c"],
        user_provider_config={},
        gate_user_id=USER,
    )
    assert chain.fallback_models == ["nous-c"]
    assert chain.platform_models == frozenset({"nous-a", "nous-c"})


async def test_fallback_primary_the_user_disabled_raises_typed(env, nous_allowed):
    from app.services.ai.llm.fallback_wiring import build_fallback_llm

    env.rows = [catalog_row("nous-a"), catalog_row("nous-b")]
    env.stored = {"disabled_models": ["nous-a"]}
    with pytest.raises(PlatformModelNotAvailableError):
        await build_fallback_llm(
            primary_model="nous-a",
            fallback_models=["nous-b"],
            user_provider_config={},
            gate_user_id=USER,
        )


async def test_fallback_without_gate_keeps_the_pool(env, nous_allowed):
    from app.services.ai.llm.fallback_wiring import build_fallback_llm

    env.rows = [catalog_row("nous-a"), catalog_row("nous-b")]
    env.stored = {"disabled_models": ["nous-a", "nous-b"]}
    chain = await build_fallback_llm(
        primary_model="nous-a",
        fallback_models=["nous-b"],
        user_provider_config={},
    )
    assert chain.fallback_models == ["nous-b"]
