"""NousImagesProvider — the nous-engine ``/images/generations`` bridge.

All network is ``httpx.MockTransport``; the bodies below are the engine's real
wire shapes (OpenAI-style ``data[].url`` success, ``{"error": {...}}``
failure), per the "边界 mock 必须用真实 JSON 形状" rule.
"""

from __future__ import annotations

import base64
import json
import os
import stat

import httpx
import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.provider_protocols.base import ProviderNotConfiguredError
from app.services.library.scratch_reaper import SCRATCH_DIR_PREFIXES
from app.services.media.parsers.video_providers.nous_images import (
    SCRATCH_PREFIX,
    NousEngineImageError,
    NousImagesProvider,
    image_data_uri,
    upscale_short_side,
)

BASE = "http://host.docker.internal:8000/v1"
SIGNED = (
    "http://host.docker.internal:8000/files/images/2026-09-23/abc.png?token=t&expires=1"
)
PNG = b"\x89PNG\r\n\x1a\nupscaled-bytes"


def _provider(handler) -> NousImagesProvider:
    return NousImagesProvider(
        base_url=BASE + "/",
        api_key="sk-instance",
        default_model="studio-upscale",
        transport=httpx.MockTransport(handler),
    )


def _src(tmp_path, name="src.png", data=b"\x89PNGsource"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected", [("2k", 1440), ("4k", 2160), ("8k", 4320), ("4K", 2160)]
)
def test_resolution_maps_to_short_side(value, expected):
    assert upscale_short_side(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "1k", "1440", "16k"])
def test_unknown_resolution_raises_instead_of_defaulting(value):
    with pytest.raises(ValueError):
        upscale_short_side(value)


@pytest.mark.unit
def test_data_uri_carries_mime_from_extension(tmp_path):
    uri = image_data_uri(_src(tmp_path, "a.jpg", b"jpegbytes"))
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"jpegbytes"


@pytest.mark.unit
def test_data_uri_refuses_a_non_image(tmp_path):
    with pytest.raises(ValueError):
        image_data_uri(_src(tmp_path, "notes.txt", b"hi"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upscale_posts_the_engine_shape_and_downloads_privately(tmp_path):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"created": 1, "data": [{"url": SIGNED}]})
        seen["download_auth"] = request.headers.get("authorization")
        assert str(request.url) == SIGNED
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    result = await _provider(handler).upscale_image(
        image_path=_src(tmp_path), resolution="4k"
    )

    assert seen["url"] == BASE + "/images/generations"
    assert seen["auth"] == "Bearer sk-instance"
    body = seen["body"]
    assert body["model"] == "studio-upscale"
    assert body["resolution"] == 2160
    assert body["image"].startswith("data:image/png;base64,")
    assert "prompt" not in body
    # The signed link is fetched WITHOUT our key.
    assert seen["download_auth"] is None

    assert result.mime == "image/png"
    with open(result.local_path, "rb") as fh:
        assert fh.read() == PNG
    parent = os.path.dirname(result.local_path)
    assert os.path.basename(parent).startswith(SCRATCH_PREFIX)
    assert SCRATCH_PREFIX in SCRATCH_DIR_PREFIXES  # the route can reap it
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(result.local_path).st_mode) == 0o600


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_model_overrides_the_default(tmp_path):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json={"created": 1, "data": [{"url": SIGNED}]})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    await _provider(handler).upscale_image(
        image_path=_src(tmp_path), resolution="2k", model="studio-upscale-v2"
    )
    assert seen["model"] == "studio-upscale-v2"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [(404, "model_not_found"), (402, "quota_exceeded"), (503, "not_ready")],
)
async def test_engine_errors_surface_status_and_code(tmp_path, status, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": f"engine says {code}",
                    "type": "invalid_request_error",
                    "code": code,
                }
            },
        )

    with pytest.raises(NousEngineImageError) as info:
        await _provider(handler).upscale_image(
            image_path=_src(tmp_path), resolution="2k"
        )
    assert info.value.status == status
    assert info.value.code == code
    assert f"engine says {code}" in str(info.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_json_error_body_still_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    with pytest.raises(NousEngineImageError) as info:
        await _provider(handler).upscale_image(
            image_path=_src(tmp_path), resolution="2k"
        )
    assert info.value.status == 500
    assert "upstream exploded" in str(info.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_without_url_is_an_error_not_an_empty_result(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"created": 1, "data": []})

    with pytest.raises(NousEngineImageError) as info:
        await _provider(handler).upscale_image(
            image_path=_src(tmp_path), resolution="2k"
        )
    assert info.value.code == "no_url"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_download_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"created": 1, "data": [{"url": SIGNED}]})
        return httpx.Response(403, text="expired")

    with pytest.raises(NousEngineImageError) as info:
        await _provider(handler).upscale_image(
            image_path=_src(tmp_path), resolution="2k"
        )
    assert info.value.status == 403
    assert info.value.code == "download_failed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_sends_prompt_shape_and_returns_the_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"created": 1, "data": [{"url": SIGNED}]})

    result = await _provider(handler).generate(
        "a cat", "studio-t2i", aspect_ratio="16:9"
    )
    assert seen["body"] == {"model": "studio-t2i", "prompt": "a cat"}
    assert result.image_url == SIGNED
    assert result.provider == "nous"


# ─────────────────────────── protocol wiring ───────────────────────────


def _row(**over) -> dict:
    row = {
        "name": "nous-studio-upscale",
        "actual_provider": "nous",
        "actual_model": "studio-upscale",
        "api_key": "sk-instance",
        "base_url": BASE,
    }
    row.update(over)
    return row


@pytest.mark.unit
def test_nous_is_an_upscale_only_image_family():
    proto = pp.resolve_generation_protocol("nous")
    assert proto is not None
    assert "image" in proto.model_types
    assert proto.upscale_capable is True
    assert proto.text_to_image is False
    # Still the chat key it always was.
    assert "nous" in pp.chat_provider_keys()


@pytest.mark.unit
def test_protocol_builds_the_bridge_for_both_hooks():
    proto = pp.resolve_generation_protocol("nous")
    for build in (proto.build_upscale_provider, proto.build_image_provider):
        provider, model = build(_row())
        assert isinstance(provider, NousImagesProvider)
        assert model == "studio-upscale"


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["api_key", "base_url"])
def test_protocol_refuses_a_row_without_credentials(missing):
    proto = pp.resolve_generation_protocol("nous")
    with pytest.raises(ProviderNotConfiguredError) as info:
        proto.build_upscale_provider(_row(**{missing: ""}))
    assert missing in str(info.value)
    assert "nous-studio-upscale" in str(info.value)
