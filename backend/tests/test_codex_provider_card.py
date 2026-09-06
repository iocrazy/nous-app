"""Codex as a STANDARD provider card (Settings → AI → Providers), 2026-09-05.

User: "要像 Doubao 这样能够配置，配置完才会在「通用」这里有个接入 AI 集合" — one
card with toggle + enabled-model chips + Test Connection, and the enabled
models then flow into the agent model picker like any other provider's.

Two facts shape the design:

* the credential is the user's OWN machine (paired daemon + ChatGPT login),
  so the card has no API key and Test Connection asks the daemon, not a URL;
* agent ``model`` values are bare names routed by PREFIX (``gpt-*`` → the
  OpenAI BYOK card). Codex models are ``gpt-*`` too, so the card's ids carry
  a ``codex:`` prefix — the same convention as ``nous:<model>`` and the ASR
  picker's ``provider:model`` — and the prefix is stripped exactly once, at
  the protocol boundary, before it becomes ``codex exec --model``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai.adapters.factory import (
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.provider_protocols.base import ProviderNotConfiguredError
from app.services.ai.provider_protocols.codex_local import CodexLocalProtocol
from app.services.codex import provider_card as card

# Bound at import time — before conftest's autouse fixture replaces the module
# attribute with an always-True stub — so the gate itself can be tested.
from app.services.codex.provider_card import (  # noqa: E402
    card_enabled as real_card_enabled,
)

USER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


# ── routing ────────────────────────────────────────────────────────────────


def test_codex_prefix_routes_to_the_local_daemon_not_openai():
    assert provider_key_for_model("codex:gpt-6-astra") == "codex-local"
    # Unchanged: a bare gpt-* name is still the OpenAI BYOK card.
    assert provider_key_for_model("gpt-6-astra") == "openai"


def test_prefix_is_stripped_once_at_the_protocol_boundary():
    adapter = CodexLocalProtocol().build_chat_adapter(
        "codex:gpt-6-astra", {}, user_id=USER
    )
    assert adapter.model == "gpt-6-astra"
    # A bare name (platform catalog row, actual_model) passes through as-is.
    assert (
        CodexLocalProtocol().build_chat_adapter("gpt-5.5", {}, user_id=USER).model
        == "gpt-5.5"
    )


def test_byok_path_reaches_the_daemon_adapter_when_a_user_is_in_hand():
    adapter = get_adapter_for_user(
        "codex:gpt-6-astra", {"codex-local": {"enabled": True}}, None, user_id=USER
    )
    assert type(adapter).__name__ == "CodexDaemonAdapter"
    assert adapter.model == "gpt-6-astra"


def test_byok_path_refuses_without_a_user_rather_than_dialing_someone():
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter_for_user(
            "codex:gpt-6-astra", {"codex-local": {"enabled": True}}, None
        )


# ── Test Connection ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_offline_is_a_typed_failure_that_says_to_pair():
    res = await card.test_codex_local_connection(
        USER,
        online_device_id=AsyncMock(return_value=None),
        list_devices=AsyncMock(return_value=[]),
    )
    assert res["success"] is False and res["models"] is None
    assert "pair" in res["error"].lower()


@pytest.mark.asyncio
async def test_connection_online_but_not_logged_in_names_the_device():
    devices = [
        {
            "id": "d1",
            "device_name": "mac-mini",
            "env_report": {"auth_ok": False, "codex_ok": True},
        }
    ]
    res = await card.test_codex_local_connection(
        USER,
        online_device_id=AsyncMock(return_value="d1"),
        list_devices=AsyncMock(return_value=devices),
    )
    assert res["success"] is False
    assert "mac-mini" in res["error"] and "login" in res["error"].lower()


@pytest.mark.asyncio
async def test_connection_ok_lists_prefixed_known_models():
    devices = [
        {
            "id": "d1",
            "device_name": "mac-mini",
            "env_report": {"auth_ok": True, "codex_ok": True},
        }
    ]
    res = await card.test_codex_local_connection(
        USER,
        online_device_id=AsyncMock(return_value="d1"),
        list_devices=AsyncMock(return_value=devices),
    )
    assert res["success"] is True and res["error"] is None
    assert res["models"] == [f"codex:{m}" for m in card.KNOWN_CODEX_MODELS]
    assert all(m.startswith("codex:") for m in res["models"])


# ── orchestrator model for image generation ────────────────────────────────


def _settings(cfg):
    return {"ai_providers": {"codex-local": cfg}} if cfg is not None else {}


@pytest.mark.asyncio
async def test_orchestrator_model_prefers_selected_then_first_enabled(monkeypatch):
    monkeypatch.setattr(
        card,
        "_load_ai_settings",
        AsyncMock(
            return_value=_settings(
                {
                    "enabled": True,
                    "enabled_models": ["codex:gpt-5.5", "codex:gpt-6-astra"],
                }
            )
        ),
    )
    assert await card.codex_orchestrator_model(USER) == "gpt-5.5"
    monkeypatch.setattr(
        card,
        "_load_ai_settings",
        AsyncMock(
            return_value=_settings(
                {
                    "enabled": True,
                    "enabled_models": ["codex:gpt-5.5", "codex:gpt-6-astra"],
                    "selected_model": "codex:gpt-6-astra",
                }
            )
        ),
    )
    assert await card.codex_orchestrator_model(USER) == "gpt-6-astra"


@pytest.mark.asyncio
async def test_orchestrator_ignores_a_selected_model_that_is_not_enabled(monkeypatch):
    """Test Connection pre-seeds ``selected_model`` with the catalog's first
    entry before any chip exists, and addEnabledModel never overwrites a
    present value — so a user who enables ONLY gpt-5.5 would still draw with
    gpt-6-astra. The chips are what the user sees; they win."""
    monkeypatch.setattr(
        card,
        "_load_ai_settings",
        AsyncMock(
            return_value=_settings(
                {
                    "enabled": True,
                    "enabled_models": ["codex:gpt-5.5"],
                    "selected_model": "codex:gpt-6-astra",
                }
            )
        ),
    )
    assert await card.codex_orchestrator_model(USER) == "gpt-5.5"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {"enabled": False, "enabled_models": ["codex:gpt-5.5"]},
        {"enabled": True, "enabled_models": []},
        {"enabled": True, "enabled_models": ["codex:bad name"]},
    ],
)
async def test_orchestrator_model_is_none_when_the_card_offers_nothing_usable(
    monkeypatch, cfg
):
    monkeypatch.setattr(
        card, "_load_ai_settings", AsyncMock(return_value=_settings(cfg))
    )
    assert await card.codex_orchestrator_model(USER) is None


@pytest.mark.asyncio
async def test_orchestrator_model_lookup_failure_never_breaks_dispatch(monkeypatch):
    monkeypatch.setattr(
        card, "_load_ai_settings", AsyncMock(side_effect=RuntimeError("db down"))
    )
    assert await card.codex_orchestrator_model(USER) is None


@pytest.mark.asyncio
async def test_local_engine_uses_the_card_model_for_codex_only(monkeypatch):
    from app.workflows import canvas_generation as wf

    rows = [
        {
            "name": "codex-local-image",
            "actual_provider": "codex-local",
            "actual_model": "gpt-6-astra",
        },
        {
            "name": "jimeng-local-image",
            "actual_provider": "jimeng-local",
            "actual_model": "",
        },
    ]
    from app.services.media.parsers.video_providers import db_registry

    monkeypatch.setattr(db_registry, "_enabled_rows", AsyncMock(return_value=rows))
    monkeypatch.setattr(db_registry, "_visible_to", lambda row, uid: True)
    monkeypatch.setattr(
        card, "codex_orchestrator_model", AsyncMock(return_value="gpt-5.5")
    )
    assert await wf._local_engine("codex-local-image", "image", USER) == (
        "codex",
        "gpt-5.5",
    )
    assert await wf._local_engine("jimeng-local-image", "image", USER) == (
        "dreamina",
        "",
    )


# ── router: Test Connection for the card goes to the daemon, not a URL ─────


@pytest.mark.asyncio
async def test_test_connection_route_dispatches_codex_local_to_the_daemon_probe(
    monkeypatch,
):
    import sys

    from httpx import ASGITransport, AsyncClient

    from app.core.deps import AuthContext, get_auth
    from app.main import app

    r = sys.modules["app.api.ai_settings_router"]

    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        probe = AsyncMock(
            return_value={
                "success": True,
                "models": ["codex:gpt-6-astra"],
                "error": None,
            }
        )
        monkeypatch.setattr(r.codex_card, "test_codex_local_connection", probe)
        factory = AsyncMock()
        monkeypatch.setattr(
            "app.services.ai.providers.ai_provider.AIProviderFactory.test_connection",
            factory,
        )
        persisted = AsyncMock(return_value=True)
        monkeypatch.setattr(r, "persist_provider_health", persisted)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.post(
                "/api/v1/ai/test-connection", json={"provider_key": "codex-local"}
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        probe.assert_awaited_once_with(USER)
        factory.assert_not_awaited()
        persisted.assert_awaited_once()
        assert persisted.await_args.args[1:3] == ("codex-local", "ok")
    finally:
        app.dependency_overrides.pop(get_auth, None)


# ── dispatch honours the card too, with a typed, actionable failure ─────────


@pytest.mark.asyncio
async def test_card_enabled_reads_the_toggle(monkeypatch):
    monkeypatch.setattr(
        card, "_load_ai_settings", AsyncMock(return_value=_settings({"enabled": True}))
    )
    assert await real_card_enabled(USER) is True
    monkeypatch.setattr(
        card, "_load_ai_settings", AsyncMock(return_value=_settings({"enabled": False}))
    )
    assert await real_card_enabled(USER) is False
    monkeypatch.setattr(card, "_load_ai_settings", AsyncMock(return_value={}))
    assert await real_card_enabled(USER) is False
    monkeypatch.setattr(
        card, "_load_ai_settings", AsyncMock(side_effect=RuntimeError("db"))
    )
    assert await real_card_enabled(USER) is False


@pytest.mark.asyncio
async def test_workflow_refuses_a_codex_run_when_the_card_is_off(monkeypatch):
    from app.services.generation.failure import describe_generation_failure
    from app.workflows import canvas_generation as wf

    monkeypatch.setattr(card, "card_enabled", AsyncMock(return_value=False))
    with pytest.raises(card.ProviderCardDisabledError) as ei:
        await wf._require_provider_card("codex", USER)
    message, patch = describe_generation_failure(ei.value)
    assert patch["failure"]["code"] == "provider_card_disabled"
    assert message.startswith("[provider_card_disabled]") and message.isascii()
    assert "provider_card_disabled" in wf.NON_RETRYABLE_FAILURE_CODES

    monkeypatch.setattr(card, "card_enabled", AsyncMock(return_value=True))
    await wf._require_provider_card("codex", USER)  # no raise
    await wf._require_provider_card("dreamina", USER)  # not gated by this card
