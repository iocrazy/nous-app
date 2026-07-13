"""Dispatch matrix for db_registry (jimeng-cli vs Ark) + the image adapter."""

from __future__ import annotations

import pytest

from app.services.ai.provider_protocols.jimeng import _JimengImageAdapter
from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.db_registry import (
    resolve_image_provider,
    resolve_video_provider,
)
from app.services.media.parsers.video_providers.jimeng_cli import (
    GenResult,
    JimengCliProvider,
)


def _row(**over):
    base = {
        "name": "row",
        "type": "image",
        "actual_provider": "jimeng-cli",
        "actual_model": "5.0",
        "api_key": "",
        "base_url": "",
        "is_enabled": True,
        "sort_order": 0,
    }
    base.update(over)
    return base


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_all(self):
        return self._rows


def _patch_repo(monkeypatch, rows):
    import app.repositories.mediahub_model_repository as repo_mod

    monkeypatch.setattr(
        repo_mod, "get_mediahub_model_repository", lambda: _FakeRepo(rows)
    )


# --------------------------------------------------------------- image dispatch
async def test_resolve_image_jimeng(monkeypatch):
    _patch_repo(monkeypatch, [_row(name="jimeng-cli-image")])
    provider, model = await resolve_image_provider()
    assert isinstance(provider, _JimengImageAdapter)
    assert model == "5.0"


async def test_resolve_image_ark(monkeypatch):
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="ark-seedream",
                actual_provider="ark",
                actual_model="doubao-seedream",
                api_key="k",
                base_url="https://ark.example/api/v3",
            )
        ],
    )
    provider, model = await resolve_image_provider()
    assert isinstance(provider, ArkImageProvider)
    assert model == "doubao-seedream"


async def test_resolve_image_prefers_jimeng_when_both_enabled(monkeypatch):
    # Ark row first by sort_order, jimeng second — jimeng must still win.
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="ark",
                actual_provider="ark",
                actual_model="ds",
                api_key="k",
                base_url="u",
                sort_order=0,
            ),
            _row(name="jimeng-cli-image", actual_model="5.0", sort_order=1),
        ],
    )
    provider, model = await resolve_image_provider()
    assert isinstance(provider, _JimengImageAdapter)
    assert model == "5.0"


async def test_resolve_image_none_enabled(monkeypatch):
    _patch_repo(monkeypatch, [_row(is_enabled=False)])
    with pytest.raises(RuntimeError, match="no image model"):
        await resolve_image_provider()


async def test_resolve_image_unknown_provider(monkeypatch):
    _patch_repo(monkeypatch, [_row(actual_provider="midjourney")])
    with pytest.raises(RuntimeError, match="No image provider implementation"):
        await resolve_image_provider()


# --------------------------------------------------------------- video dispatch
async def test_resolve_video_jimeng(monkeypatch):
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="jimeng-cli-seedance", type="video", actual_model="seedance2.0fast"
            )
        ],
    )
    provider, model = await resolve_video_provider()
    assert isinstance(provider, JimengCliProvider)
    assert model == "seedance2.0fast"


async def test_resolve_video_none_enabled(monkeypatch):
    _patch_repo(monkeypatch, [_row(type="image")])  # no video row
    with pytest.raises(RuntimeError, match="no video model"):
        await resolve_video_provider()


async def test_resolve_video_unknown_provider(monkeypatch):
    _patch_repo(monkeypatch, [_row(type="video", actual_provider="runway")])
    with pytest.raises(RuntimeError, match="No video provider implementation"):
        await resolve_video_provider()


# --------------------------------------------------------------- adapter shape
async def test_jimeng_image_adapter_returns_local_path(monkeypatch):
    provider = JimengCliProvider()

    async def fake_generate_image(*, prompt, aspect, model_version):
        assert aspect == "16:9"
        assert model_version == "5.0"
        return GenResult(
            local_path="/tmp/out.png", mime="image/png", raw={"submit_id": "s"}
        )

    monkeypatch.setattr(provider, "generate_image", fake_generate_image)
    adapter = _JimengImageAdapter(provider)
    result = await adapter.generate("a cat", "5.0", aspect_ratio="16:9")

    assert result.image_url == ""  # no URL — local file only
    assert result.image_path == "/tmp/out.png"
    assert result.provider == "jimeng-cli"
    assert result.metadata["mime"] == "image/png"
