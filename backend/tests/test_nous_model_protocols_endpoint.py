"""GET protocols endpoint returns the registry. Called directly (admin
endpoint convention), not via TestClient."""

from __future__ import annotations

import pytest

from app.api.admin.nous_model_router import list_provider_protocols


@pytest.mark.asyncio
async def test_protocols_endpoint_returns_registry():
    resp = await list_provider_protocols(auth=object())  # AdminAuthDep unused in body
    keys = {p.key for p in resp.protocols}
    # Every chat + generation protocol is present.
    assert {
        "qwen",
        "openai",
        "claude",
        "deepseek",
        "doubao",
        "modelscope",
        "ark",
        "jimeng-cli",
    } <= keys
    qwen = next(p for p in resp.protocols if p.key == "qwen")
    assert qwen.is_default is True
    assert "llm" in qwen.model_types
    jimeng = next(p for p in resp.protocols if p.key == "jimeng-cli")
    assert "jimeng" in jimeng.aliases
    assert "image" in jimeng.model_types


@pytest.mark.asyncio
async def test_protocols_endpoint_ships_the_former_byok_only_keys():
    """The five keys that used to live in a side dict next to the provider
    registry are protocols now, so the admin dropdown can file rows under them.
    volcengine is offered for ASR only — never as a chat provider."""
    resp = await list_provider_protocols(auth=object())
    by_key = {p.key: p for p in resp.protocols}
    assert {"kimi", "minimax", "ollama", "lmstudio", "volcengine"} <= set(by_key)
    assert by_key["volcengine"].model_types == ["asr"]
    for key in ("kimi", "minimax", "ollama", "lmstudio"):
        assert by_key[key].model_types == ["llm"]
    assert by_key["ollama"].credential_kind == "endpoint"


@pytest.mark.asyncio
async def test_new_protocols_sit_next_to_their_families():
    """Admin cards sort by registry declaration order (protocolRank). The
    speech key sits right after the doubao chat key — same vendor, different
    product — and the two self-hosted endpoints sit together."""
    keys = [p.key for p in (await list_provider_protocols(auth=object())).protocols]
    assert keys.index("volcengine") == keys.index("doubao") + 1
    assert keys.index("lmstudio") == keys.index("ollama") + 1
    assert keys.index("ollama") < keys.index("ark")
