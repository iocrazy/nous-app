"""Tests for the Ark (Volcengine / 豆包) text-to-image provider + its DB-catalog
resolver — the missing foundation that made the image ``provider_registry`` an
empty shell (every image generation KeyErrored).

Covers:
  - ArkImageProvider.generate happy path (respx-mocked httpx) + aspect_ratio→size
  - generate raises on non-200 and on a missing url
  - resolve_image_provider picks the enabled image row + decrypts (revealed) key
  - resolve_image_provider prefers a name/actual_model match, else first enabled
  - resolve_image_provider clear error when no image row / unsupported provider
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.base import ImageGenResult
from app.services.media.parsers.video_providers.db_registry import (
    resolve_image_provider,
)

pytestmark = pytest.mark.unit

_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_GEN_URL = f"{_BASE}/images/generations"


# ---------------------------------------------------------------------------
# ArkImageProvider.generate
# ---------------------------------------------------------------------------


@respx.mock
async def test_generate_happy_path_maps_aspect_ratio_and_parses_url():
    route = respx.post(_GEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "doubao-seedream",
                "data": [
                    {"url": "https://cdn.ark/img.png", "revised_prompt": "a cat, hd"}
                ],
            },
        )
    )
    provider = ArkImageProvider(
        api_key="secret-key", base_url=_BASE, default_model="doubao-seedream"
    )

    result = await provider.generate("a cat", "doubao-seedream", aspect_ratio="16:9")

    assert isinstance(result, ImageGenResult)
    assert result.image_url == "https://cdn.ark/img.png"
    assert result.provider == "ark"
    assert result.model == "doubao-seedream"
    # 16:9 mapped to a known Ark size; dims parsed off it.
    assert result.metadata["size"] == "1280x720"
    assert result.width == 1280 and result.height == 720
    assert result.metadata["revised_prompt"] == "a cat, hd"

    # Bearer auth + the OpenAI-compatible request body.
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer secret-key"
    import json as _json

    body = _json.loads(sent.content)
    assert body == {
        "model": "doubao-seedream",
        "prompt": "a cat",
        "size": "1280x720",
        "response_format": "url",
    }


@respx.mock
async def test_generate_explicit_size_kwarg_wins_over_aspect_ratio():
    respx.post(_GEN_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://cdn/x.png"}]})
    )
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    result = await provider.generate("p", "m", aspect_ratio="16:9", size="864x1152")
    assert result.metadata["size"] == "864x1152"
    assert (result.width, result.height) == (864, 1152)


@respx.mock
async def test_generate_raises_on_non_200():
    respx.post(_GEN_URL).mock(
        return_value=httpx.Response(402, text="insufficient balance")
    )
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    with pytest.raises(RuntimeError, match="402"):
        await provider.generate("p", "m")


@respx.mock
async def test_generate_raises_when_no_url_in_data():
    respx.post(_GEN_URL).mock(return_value=httpx.Response(200, json={"data": [{}]}))
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    with pytest.raises(RuntimeError, match="no url"):
        await provider.generate("p", "m")


@respx.mock
async def test_generate_raises_when_data_empty():
    respx.post(_GEN_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    with pytest.raises(RuntimeError, match="no url"):
        await provider.generate("p", "m")


async def test_check_status_not_implemented_sync_api():
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    with pytest.raises(NotImplementedError):
        await provider.check_status("任意")


def test_list_models_returns_default():
    provider = ArkImageProvider(api_key="k", base_url=_BASE, default_model="m")
    assert provider.list_models() == ["m"]


def test_ctor_requires_key_and_base_url():
    with pytest.raises(ValueError):
        ArkImageProvider(api_key="", base_url=_BASE, default_model="m")
    with pytest.raises(ValueError):
        ArkImageProvider(api_key="k", base_url="", default_model="m")


# ---------------------------------------------------------------------------
# resolve_image_provider (DB catalog)
# ---------------------------------------------------------------------------


def _repo_with(rows):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    return repo


async def test_resolve_picks_enabled_image_row_and_reveals_key():
    rows = [
        {"name": "mediahub-doubao-llm", "type": "llm", "is_enabled": True},
        {
            "name": "mediahub-doubao-seedream",
            "type": "image",
            "is_enabled": True,
            "actual_provider": "doubao",
            "actual_model": "doubao-seedream-3-0",
            "base_url": _BASE,
            # list_all's _row() already revealed the encrypted key to plaintext.
            "api_key": "revealed-plaintext-key",
        },
    ]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        MagicMock(return_value=_repo_with(rows)),
    ):
        provider, actual_model = await resolve_image_provider(None)

    assert isinstance(provider, ArkImageProvider)
    assert actual_model == "doubao-seedream-3-0"
    assert provider._api_key == "revealed-plaintext-key"
    assert provider._base_url == _BASE


async def test_resolve_prefers_name_match_over_first_enabled():
    rows = [
        {
            "name": "mediahub-image-a",
            "type": "image",
            "is_enabled": True,
            "actual_provider": "ark",
            "actual_model": "model-a",
            "base_url": _BASE,
            "api_key": "ka",
        },
        {
            "name": "mediahub-image-b",
            "type": "image",
            "is_enabled": True,
            "actual_provider": "ark",
            "actual_model": "model-b",
            "base_url": _BASE,
            "api_key": "kb",
        },
    ]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        MagicMock(return_value=_repo_with(rows)),
    ):
        # by catalog name
        _, model_by_name = await resolve_image_provider("mediahub-image-b")
        # by actual_model
        _, model_by_actual = await resolve_image_provider("model-b")
        # a registry name that never lives in the catalog → first enabled row
        _, model_miss = await resolve_image_provider("openai")

    assert model_by_name == "model-b"
    assert model_by_actual == "model-b"
    assert model_miss == "model-a"


async def test_resolve_skips_disabled_and_non_image_rows():
    rows = [
        {"name": "x", "type": "image", "is_enabled": False},  # disabled
        {"name": "y", "type": "llm", "is_enabled": True},  # wrong type
        {
            "name": "z",
            "type": "image",
            "is_enabled": True,
            "actual_provider": "doubao",
            "actual_model": "good",
            "base_url": _BASE,
            "api_key": "k",
        },
    ]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        MagicMock(return_value=_repo_with(rows)),
    ):
        _, model = await resolve_image_provider(None)
    assert model == "good"


async def test_resolve_raises_clear_error_when_no_image_row():
    rows = [{"name": "y", "type": "llm", "is_enabled": True}]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        MagicMock(return_value=_repo_with(rows)),
    ):
        with pytest.raises(RuntimeError, match="no image model configured"):
            await resolve_image_provider(None)


async def test_resolve_raises_on_unsupported_provider_impl():
    rows = [
        {
            "name": "mediahub-image-oai",
            "type": "image",
            "is_enabled": True,
            "actual_provider": "openai",  # no wired image impl
            "actual_model": "dall-e-3",
            "base_url": "https://api.openai.com/v1",
            "api_key": "k",
        }
    ]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        MagicMock(return_value=_repo_with(rows)),
    ):
        with pytest.raises(RuntimeError, match="No image provider implementation"):
            await resolve_image_provider(None)


# ---------------------------------------------------------------------------
# Consumer wiring: ImageGenerationService.generate_image DB-catalog fallback
# ---------------------------------------------------------------------------


async def _generate_image_with(model: str):
    """Run generate_image against an EMPTY registry (registry miss → DB
    fallback) with a stubbed resolver returning a fake Ark provider whose
    actual_model is 'doubao-seedream-actual'. Returns (result, gen_model used)."""
    from app.services.ai.media.image_generation_service import ImageGenerationService

    fake_provider = MagicMock()
    fake_provider.generate = AsyncMock(
        return_value=ImageGenResult(image_url="https://cdn/out.png", provider="ark")
    )
    resolver = AsyncMock(return_value=(fake_provider, "doubao-seedream-actual"))

    svc = ImageGenerationService()
    # The empty registry naturally KeyErrors, driving the DB-catalog fallback
    # branch.
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry."
            "resolve_image_provider",
            resolver,
        ),
    ):
        result = await svc.generate_image(
            project_id="",
            node_id="shot-1",
            prompt="a cat",
            model=model,
            provider_name=None,  # workflow now threads None → DB resolution
        )
    resolver.assert_awaited_once_with(None)
    gen_model = fake_provider.generate.call_args.args[1]
    return result, gen_model


async def test_generate_image_db_fallback_default_model_yields_to_catalog():
    # The legacy 'dall-e-3' default yields to the resolved catalog actual_model.
    result, gen_model = await _generate_image_with("dall-e-3")
    assert result["image_url"] == "https://cdn/out.png"
    assert gen_model == "doubao-seedream-actual"


async def test_generate_image_db_fallback_explicit_model_is_kept():
    # An explicit non-default caller model wins over the catalog actual_model.
    _, gen_model = await _generate_image_with("my-custom-image-model")
    assert gen_model == "my-custom-image-model"
