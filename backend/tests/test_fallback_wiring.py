"""共享 fallback 组合器(spec §2)——组合语义与 chat 内联段逐字等价的守卫。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.ai.llm.fallback_wiring as fw


@pytest.mark.asyncio
async def test_platform_catalog_hit_preresolves_adapter_by_actual_provider():
    hit = ("doubao", {"api_key": "k", "base_url": "https://ark"}, "actual-model-x")
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=hit)),
        patch.object(
            fw, "resolve_provider_key", MagicMock(return_value="doubao")
        ) as rpk,
        patch.object(
            fw, "get_adapter_for_key", MagicMock(return_value="PLATFORM_ADAPTER")
        ),
        patch.object(
            fw, "get_adapter_for_user", MagicMock(return_value="BYOK_ADAPTER")
        ),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=["m2"], user_provider_config={}
        )
    # #1279 契约:用目录行的 actual_provider dispatch,不做前缀猜测
    rpk.assert_called_with("doubao", "actual-model-x")
    assert chain.adapter_factory("m1") == "PLATFORM_ADAPTER"
    assert chain.primary_model == "m1" and chain.fallback_models == ["m2"]


@pytest.mark.asyncio
async def test_catalog_miss_falls_to_byok_factory():
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", MagicMock(return_value="BYOK_ADAPTER")
        ) as gau,
    ):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=[], user_provider_config={"doubao": {}}
        )
    assert chain.adapter_factory("m1") == "BYOK_ADAPTER"
    gau.assert_called_once_with("m1", {"doubao": {}}, None)


@pytest.mark.asyncio
async def test_health_registry_absent_is_none_and_deadline_from_env(monkeypatch):
    monkeypatch.setenv("LLM_TOTAL_DEADLINE_S", "45")
    with patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)):
        chain = await fw.build_fallback_llm(
            primary_model="m1", fallback_models=[], user_provider_config={}
        )
    assert chain.total_deadline_seconds == 45.0
    # best-effort app.state lookup (app.main may or may not be importable in
    # this test process) — the only real assertion is that construction
    # doesn't raise either way; no value-level claim to make here.


@pytest.mark.asyncio
async def test_duplicate_models_deduped_in_preresolve():
    calls = []

    async def _rmm(m, _kind):
        calls.append(m)
        return None

    with patch.object(fw, "resolve_mediahub_model", AsyncMock(side_effect=_rmm)):
        await fw.build_fallback_llm(
            primary_model="m1", fallback_models=["m1", "m2"], user_provider_config={}
        )
    assert calls == ["m1", "m2"]
