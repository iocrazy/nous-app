"""Dispatch + BaseImageProvider adapter tests for the codex protocol."""

from __future__ import annotations

from app.services.ai.provider_protocols.codex import _CodexImageAdapter
from app.services.media.parsers.video_providers.codex_cli import (
    CodexCliProvider,
    GenResult,
)
from app.services.media.parsers.video_providers.db_registry import (
    resolve_image_provider,
)


def _row(**over):
    base = {
        "name": "codex-image",
        "type": "image",
        "actual_provider": "codex",
        "actual_model": "gpt-5.4",
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


async def test_resolve_image_codex(monkeypatch):
    _patch_repo(monkeypatch, [_row()])
    provider, model = await resolve_image_provider("codex-image")
    assert isinstance(provider, _CodexImageAdapter)
    assert model == "gpt-5.4"


async def test_explicit_codex_wins_over_default_jimeng_preference(monkeypatch):
    jimeng_row = _row(
        name="jimeng-cli-image", actual_provider="jimeng-cli", actual_model="5.0"
    )
    _patch_repo(monkeypatch, [jimeng_row, _row()])
    provider, model = await resolve_image_provider("codex-image")
    assert isinstance(provider, _CodexImageAdapter)
    assert model == "gpt-5.4"


class _StubProvider(CodexCliProvider):
    def __init__(self):
        super().__init__(bin_path="stub")
        self.calls = []

    async def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        return GenResult(local_path="/tmp/x.png", mime="image/png", raw={"ok": True})


async def test_adapter_maps_kwargs_onto_provider():
    stub = _StubProvider()
    adapter = _CodexImageAdapter(stub)

    result = await adapter.generate(
        "a red apple", "gpt-5.4", aspect_ratio="16:9", reference_image_url=None
    )

    assert result.image_path == "/tmp/x.png"
    assert result.image_url == ""
    assert result.provider == "codex"
    assert stub.calls[0]["prompt"] == "a red apple"
    assert stub.calls[0]["aspect"] == "16:9"
    assert stub.calls[0]["model_version"] == "gpt-5.4"
    assert stub.calls[0]["ref_image_path"] is None


async def test_adapter_passes_local_ref_path_through(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n")
    stub = _StubProvider()
    adapter = _CodexImageAdapter(stub)

    await adapter.generate("p", "", aspect_ratio="1:1", reference_image_url=str(ref))

    assert stub.calls[0]["ref_image_path"] == str(ref)


async def test_adapter_drops_http_reference_url(tmp_path):
    # A remote URL cannot feed `images edit --ref-image` (local file contract);
    # the adapter degrades to plain generate rather than failing the shot.
    stub = _StubProvider()
    adapter = _CodexImageAdapter(stub)

    await adapter.generate(
        "p", "", aspect_ratio="1:1", reference_image_url="https://x/y.png"
    )

    assert stub.calls[0]["ref_image_path"] is None
