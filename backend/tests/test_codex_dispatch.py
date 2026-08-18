"""Dispatch + BaseImageProvider adapter tests for the codex protocol."""

from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# Owner scoping at dispatch (fail-closed)
# ---------------------------------------------------------------------------
_OWNER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


async def test_owned_row_resolves_for_its_owner(monkeypatch):
    _patch_repo(monkeypatch, [_row(owner_user_id=_OWNER)])
    provider, model = await resolve_image_provider("codex-image", user_id=_OWNER)
    assert isinstance(provider, _CodexImageAdapter)
    assert model == "gpt-5.4"


async def test_owned_row_rejected_for_other_user(monkeypatch):
    _patch_repo(monkeypatch, [_row(owner_user_id=_OWNER)])
    with pytest.raises(RuntimeError, match="private"):
        await resolve_image_provider("codex-image", user_id="someone-else")


async def test_owned_row_rejected_without_user(monkeypatch):
    # A call path that never threads user_id must NOT reach a private provider.
    _patch_repo(monkeypatch, [_row(owner_user_id=_OWNER)])
    with pytest.raises(RuntimeError, match="private"):
        await resolve_image_provider("codex-image")


async def test_owned_row_never_silently_falls_back(monkeypatch):
    # Explicitly asking for a private row must raise, not quietly dispatch to
    # whatever public row happens to exist (silent-substitution trap).
    public = _row(
        name="jimeng-cli-image", actual_provider="jimeng-cli", actual_model="5.0"
    )
    _patch_repo(monkeypatch, [public, _row(owner_user_id=_OWNER)])
    with pytest.raises(RuntimeError, match="private"):
        await resolve_image_provider("codex-image", user_id="someone-else")


async def test_unowned_rows_resolve_for_anyone(monkeypatch):
    _patch_repo(monkeypatch, [_row(owner_user_id=None)])
    provider, _ = await resolve_image_provider("codex-image", user_id="anyone")
    assert isinstance(provider, _CodexImageAdapter)


async def test_video_dispatch_honors_owner(monkeypatch):
    from app.services.media.parsers.video_providers.db_registry import (
        resolve_video_provider,
    )

    jimeng_video = _row(
        name="jimeng-cli-seedance",
        type="video",
        actual_provider="jimeng-cli",
        actual_model="seedance2.0fast",
        owner_user_id=_OWNER,
    )
    _patch_repo(monkeypatch, [jimeng_video])
    provider, model = await resolve_video_provider(
        "jimeng-cli-seedance", user_id=_OWNER
    )
    assert model == "seedance2.0fast"
    with pytest.raises(RuntimeError):
        await resolve_video_provider("jimeng-cli-seedance", user_id="someone-else")


# ---------------------------------------------------------------------------
# user_id threading: service → resolver
# ---------------------------------------------------------------------------
async def test_image_service_threads_user_id_to_resolver(monkeypatch):
    from app.services.ai.media.image_generation_service import ImageGenerationService
    from app.services.media.parsers.video_providers import db_registry
    from app.services.media.parsers.video_providers.base import ImageGenResult

    captured = {}

    class _StubAdapter:
        async def generate(self, prompt, model, **kwargs):
            return ImageGenResult(image_url="https://x/y.png", provider="stub")

    async def fake_resolve(name=None, *, user_id=None):
        captured["name"] = name
        captured["user_id"] = user_id
        return _StubAdapter(), "gpt-5.4"

    monkeypatch.setattr(db_registry, "resolve_image_provider", fake_resolve)

    svc = ImageGenerationService()
    await svc.generate_image(
        project_id="",
        node_id="n1",
        prompt="p",
        model="",
        provider_name="codex-image",
        user_id=_OWNER,
    )
    assert captured["user_id"] == _OWNER
