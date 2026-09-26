"""``capabilities_for_model`` — one model name → one provider's capabilities.

The lookup the asset bundle uses. It reads the platform provider view
(``platform_rows(user_id, purpose="picker")``) — the same computation as the
Settings card and the generation pickers — so the catalog is stubbed at the
view's own seam (``list_enabled_private`` full rows, governance, engine). The
service-level bundle tests stub this function whole, so this file is the only
place that proves a hidden model is actually refused.

Failures it exists to make loud:

* a model the picker HIDES (blacklist, master switch, governance off, engine
  no longer listing it) coming back with capabilities — the bundle would then
  trim for a provider the user cannot choose;
* a model that IS visible but whose protocol does not resolve coming back as
  ``None`` — "you may not use this" and "this provider takes no references" are
  different answers and must not share one;
* a chat row answering at all — an llm has no reference ceiling to report.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai.provider_protocols.base import ProviderCapabilities
from app.services.generation.model_capabilities import (
    capabilities_for_model,
    generation_rows_for,
)
from tests.services.ai.test_platform_provider import (
    Env,
    catalog_row,
    engine_row,
    listed,
)

USER = "11111111-1111-1111-1111-111111111111"


def _rows() -> list[dict]:
    return [
        catalog_row("codex-local-image", type="image", actual_provider="codex-local"),
        catalog_row("ark-t2i", type="image", actual_provider="ark"),
        catalog_row("made-up-provider", type="image", actual_provider="nope"),
        catalog_row("some-chat-model", type="llm", actual_provider="deepseek"),
    ]


@pytest.fixture
def env(monkeypatch):
    e = Env(monkeypatch)
    e.rows = _rows()
    e.stored = {}
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(side_effect=lambda _u: e.stored),
    )
    return e


@pytest.mark.asyncio
async def test_a_visible_model_answers_its_protocols_capabilities(env):
    caps = await capabilities_for_model("codex-local-image", USER)
    assert caps is not None and caps.max_refs == 9
    env.repo.list_enabled_private.assert_awaited_with(USER)


@pytest.mark.asyncio
async def test_a_text_to_image_only_provider_answers_zero_not_none(env):
    """ark declares ``max_refs=0``. That is an ANSWER — the bundle drops every
    reference with ``provider_no_refs`` — not a missing model."""
    caps = await capabilities_for_model("ark-t2i", USER)
    assert caps is not None and caps.max_refs == 0


@pytest.mark.asyncio
async def test_a_visible_model_with_no_protocol_is_the_restrictive_default(env):
    caps = await capabilities_for_model("made-up-provider", USER)
    assert caps is ProviderCapabilities.none()


@pytest.mark.asyncio
async def test_an_unknown_name_is_none(env):
    assert await capabilities_for_model("no-such-model", USER) is None


@pytest.mark.asyncio
async def test_a_blank_name_is_none_without_touching_the_catalog(env):
    assert await capabilities_for_model("", USER) is None
    env.repo.list_enabled_private.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_model_the_user_blacklisted_is_none(env):
    """The binding one. A blacklisted model must be refused, not bundled — the
    picker does not offer it, so a bundle for it is a trim nobody can explain.
    """
    env.stored = {"disabled_models": ["codex-local-image"]}
    assert await capabilities_for_model("codex-local-image", USER) is None
    assert await capabilities_for_model("ark-t2i", USER) is not None


@pytest.mark.asyncio
async def test_master_switch_off_hides_every_model(env):
    env.stored = {"enabled": False}
    assert await capabilities_for_model("ark-t2i", USER) is None


@pytest.mark.asyncio
async def test_governance_off_hides_every_model(env):
    env.governance = False
    assert await capabilities_for_model("ark-t2i", USER) is None


@pytest.mark.asyncio
async def test_a_service_the_engine_no_longer_lists_is_none(env):
    # video: a nous-engine IMAGE row is upscale-only, which the picker
    # leaves out for a different reason.
    env.rows = [
        engine_row("nous-gone", "gone-t2v", type="video"),
        engine_row("nous-here", "here-t2v", type="video"),
    ]
    env.engine_answers = [listed(("here-t2v", True))]
    assert await capabilities_for_model("nous-gone", USER) is None
    assert await capabilities_for_model("nous-here", USER) is not None


@pytest.mark.asyncio
async def test_a_chat_row_never_answers(env):
    assert await capabilities_for_model("some-chat-model", USER) is None


@pytest.mark.asyncio
async def test_rows_are_the_picker_rows(env):
    """The bundle and the pickers read one row set: generation rows of the
    user's view, upscale-only rows left out."""
    env.stored = {"disabled_models": ["ark-t2i"]}
    names = {r["name"] for r in await generation_rows_for(USER)}
    assert names == {"codex-local-image", "made-up-provider"}
