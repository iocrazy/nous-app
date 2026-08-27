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


# ---------------------------------------------------------------------------
# Batch-style (provider_key=... kwarg) branch — caption/classify/translate.
# #1810 遗留缺口补课(spec §1-F4.3): 这五个测试钉死 build_fallback_llm 在
# provider_key kwarg 存在时的 flat-config 包裹 / 派生 / 降级契约，供 F1-F3
# 接线任务依赖。不改实现，只补测试。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_explicit_provider_key_wraps_flat_config():
    flat_cfg = {"api_key": "sk-x", "base_url": "https://ark.example", "app_id": "app-1"}
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", MagicMock(return_value="SCOPED_ADAPTER")
        ) as gau,
    ):
        chain = await fw.build_fallback_llm(
            primary_model="doubao-seed-1-6",
            fallback_models=[],
            user_provider_config=flat_cfg,
            provider_key="doubao",
            module="caption",
        )
    assert chain.adapter_factory("doubao-seed-1-6") == "SCOPED_ADAPTER"
    gau.assert_called_once_with(
        "doubao-seed-1-6",
        {
            "doubao": {
                "api_key": "sk-x",
                "base_url": "https://ark.example",
                "app_id": "app-1",
            }
        },
        None,
    )


@pytest.mark.asyncio
async def test_batch_empty_provider_key_derives_from_model_prefix():
    flat_cfg = {"api_key": "sk-y", "base_url": "https://ark.example"}
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", MagicMock(return_value="DERIVED_ADAPTER")
        ) as gau,
    ):
        chain = await fw.build_fallback_llm(
            primary_model="doubao-x",
            fallback_models=[],
            user_provider_config=flat_cfg,
            provider_key="",
            module="summarization",
        )
    assert chain.adapter_factory("doubao-x") == "DERIVED_ADAPTER"
    gau.assert_called_once_with(
        "doubao-x",
        {
            "doubao": {
                "api_key": "sk-y",
                "base_url": "https://ark.example",
                "app_id": "",
            }
        },
        None,
    )


@pytest.mark.asyncio
async def test_batch_unresolvable_prefix_flat_degrades():
    flat_cfg = {"api_key": "sk-z", "base_url": "https://generic.example"}
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw,
            "get_adapter_for_user",
            MagicMock(side_effect=AssertionError("should not be called")),
        ),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="mystery-model-9",
            fallback_models=[],
            user_provider_config=flat_cfg,
            provider_key="",
            module="translation",
        )
        adapter = chain.adapter_factory("mystery-model-9")
    # unknown prefix on an unresolved key must NOT reach get_adapter_for_user
    # (asserted above via side_effect) — it degrades straight to a generic
    # OpenAI-compatible adapter, mirroring the deleted _build_adapter.
    assert isinstance(adapter, fw.OpenAICompatibleAdapter)
    assert adapter.api_key == "sk-z"
    assert adapter.api_url == "https://generic.example/chat/completions"
    assert not hasattr(adapter, "app_id")


@pytest.mark.asyncio
async def test_batch_get_adapter_for_user_valueerror_flat_degrades():
    flat_cfg = {"api_key": "sk-w", "base_url": "https://fallback.example"}
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", MagicMock(side_effect=ValueError("boom"))
        ),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="doubao-seed-1-6",
            fallback_models=[],
            user_provider_config=flat_cfg,
            provider_key="doubao",
            module="classification",
        )
        adapter = chain.adapter_factory("doubao-seed-1-6")
    assert isinstance(adapter, fw.OpenAICompatibleAdapter)
    assert adapter.api_key == "sk-w"
    assert adapter.api_url == "https://fallback.example/chat/completions"


@pytest.mark.asyncio
async def test_batch_module_kwarg_passed_to_resolve_mediahub_model():
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)) as rmm,
        patch.object(fw, "get_adapter_for_user", MagicMock(return_value="ADAPTER")),
    ):
        await fw.build_fallback_llm(
            primary_model="doubao-seed-1-6",
            fallback_models=["doubao-seed-fallback"],
            user_provider_config={"api_key": "k", "base_url": "https://ark"},
            provider_key="doubao",
            module="caption",
        )
    rmm.assert_any_call("doubao-seed-1-6", "caption")
    rmm.assert_any_call("doubao-seed-fallback", "caption")


@pytest.mark.asyncio
async def test_user_id_reaches_get_adapter_for_key():
    hit = ("codex-local", {"api_key": "", "base_url": ""}, "")
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=hit)),
        patch.object(fw, "resolve_provider_key", MagicMock(return_value="codex-local")),
        patch.object(fw, "get_adapter_for_key", MagicMock(return_value="LOCAL")) as gak,
        patch.object(fw, "get_adapter_for_user", MagicMock(return_value="BYOK")),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="Codex (Local)",
            fallback_models=[],
            user_provider_config={},
            user_id="u7",
        )
    gak.assert_called_once()
    assert gak.call_args.kwargs.get("user_id") == "u7"
    assert chain.adapter_factory("Codex (Local)") == "LOCAL"
