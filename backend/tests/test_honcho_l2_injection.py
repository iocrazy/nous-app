"""Honcho L2 injection (Phase 4 — canvas plan, read side).

The write side (write_memory step 4) has been feeding Honcho since M5;
this closes the loop: each chat turn fetches the peer's working
representation (POST /representation — pure DB read, no LLM, unlike the
slow dialectic /chat) and the composer injects it as a <user_context>
block after the cache boundary.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai.memory.honcho_memory import (
    HonchoMemoryConfig,
    HonchoMemoryService,
)

_REPRESENTATION = (
    "## Explicit Observations\n"
    "[2026-06-12] user prefers night-time neon street-food videos\n"
)


def _service(handler, *, enabled: bool = True) -> HonchoMemoryService:
    config = HonchoMemoryConfig(
        enabled=enabled, base_url="http://honcho.test", workspace_id="mediahub"
    )
    client = httpx.AsyncClient(
        base_url="http://honcho.test", transport=httpx.MockTransport(handler)
    )
    return HonchoMemoryService(config=config, client=client)


# ============================================================
# Client: get_user_representation
# ============================================================


@pytest.mark.asyncio
async def test_representation_fetched_from_peer_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"representation": _REPRESENTATION})

    result = await _service(handler).get_user_representation(user_id="u1")
    assert result == _REPRESENTATION.strip()
    assert seen == ["/v3/workspaces/mediahub/peers/user-u1/representation"]


@pytest.mark.asyncio
async def test_representation_workspace_override() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"representation": _REPRESENTATION})

    await _service(handler).get_user_representation(
        user_id="u1", workspace_id="team-42"
    )
    assert seen == ["/v3/workspaces/team-42/peers/user-u1/representation"]


@pytest.mark.asyncio
async def test_representation_none_when_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    result = await _service(handler, enabled=False).get_user_representation(
        user_id="u1"
    )
    assert result is None


@pytest.mark.asyncio
async def test_representation_none_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    assert await _service(handler).get_user_representation(user_id="u1") is None


@pytest.mark.asyncio
async def test_representation_none_when_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"representation": "  "})

    assert await _service(handler).get_user_representation(user_id="u1") is None


@pytest.mark.asyncio
async def test_representation_truncated_to_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"representation": "x" * 10_000})

    result = await _service(handler).get_user_representation(
        user_id="u1", max_chars=100
    )
    assert result is not None and len(result) == 100


@pytest.mark.asyncio
async def test_representation_transport_error_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await _service(handler).get_user_representation(user_id="u1") is None


# ============================================================
# Composer: <user_context> block
# ============================================================


@pytest.mark.asyncio
async def test_composer_injects_user_context_after_cache_boundary() -> None:
    from app.services.ai.prompts.prompt_composer import PromptComposer

    composer = PromptComposer(None, None)
    rendered = composer._assemble_system_message(
        agent={
            "id": "00000000-0000-0000-0000-000000000001",
            "slug": "script_ai",
            "identity_md": "I am script AI.",
        },
        skills=[],
        request_instructions="",
        user_context=_REPRESENTATION,
    )
    assert "<user_context>" in rendered
    assert "neon street-food" in rendered
    boundary = rendered.index("CACHE_BOUNDARY")
    assert rendered.index("<user_context>") > boundary


@pytest.mark.asyncio
async def test_composer_omits_block_when_absent() -> None:
    from app.services.ai.prompts.prompt_composer import PromptComposer

    composer = PromptComposer(None, None)
    rendered = composer._assemble_system_message(
        agent={
            "id": "00000000-0000-0000-0000-000000000001",
            "slug": "script_ai",
            "identity_md": "I am script AI.",
        },
        skills=[],
        request_instructions="",
        user_context=None,
    )
    assert "<user_context>" not in rendered


def test_dynamic_fingerprint_varies_with_user_context() -> None:
    from app.services.ai.prompts.prompt_composer import PromptComposer

    composer = PromptComposer(None, None)
    base = composer._dynamic_fingerprint("prefix", [], [], user_context=None)
    with_ctx = composer._dynamic_fingerprint(
        "prefix", [], [], user_context=_REPRESENTATION
    )
    assert base != with_ctx


# ============================================================
# Wiring: _safe_recall_honcho_context fail-open
# ============================================================


@pytest.mark.asyncio
async def test_safe_recall_returns_none_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    monkeypatch.delenv("FEATURE_HONCHO_MEMORY", raising=False)
    import app.services.ai.memory.honcho_memory as hm

    monkeypatch.setattr(hm, "_service", None)
    result = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)
    assert result is None


@pytest.mark.asyncio
async def test_safe_recall_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.ai.memory.honcho_memory as hm
    from app.services.ai.chat import ai_library_chat_wiring as wiring

    class _Boom:
        async def get_user_representation(self, **kwargs):
            raise RuntimeError("explode")

    monkeypatch.setattr(hm, "get_honcho_memory_service", lambda: _Boom())
    result = await wiring._safe_recall_honcho_context(user_id="u1", session_id=None)
    assert result is None
