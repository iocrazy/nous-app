"""GET protocols endpoint returns the registry. Called directly (admin
endpoint convention), not via TestClient."""

from __future__ import annotations

import pytest

from app.api.admin.mediahub_model_router import list_provider_protocols


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
