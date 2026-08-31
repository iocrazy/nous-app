"""``ImageGenerationService.generate_image`` forwards reference paths (P2 T4).

This hop has no other coverage. Every consumer test either replaces the whole
service with a fake that swallows ``**kwargs`` (the assets generate-slot suite)
or calls an adapter / ``provider.generate`` directly (``test_codex_dispatch``,
the canvas workflow tests) — so deleting ``reference_image_paths`` from the real
signature, or dropping the forward at the single ``image_provider.generate``
call site, would leave those suites green while production either raises
``TypeError`` or silently generates a character sheet that ignored the
character.

That is the failure the generate-slot endpoint was already fixed for once: no
image adapter pulls a remote reference url (ark accepts the kwarg and does not
send it, jimeng never reads it, codex uses a url only when it names a local
file), so LOCAL PATHS are the only channel that reaches a provider. These tests
drive the REAL service against a stub provider on both resolution branches —
registry hit and DB-catalog miss — because both funnel through the same call
site and a regression in either is invisible from the consumer suites.

No network: the provider is a stub and both resolvers are monkeypatched.
"""

from __future__ import annotations

import pytest

from app.services.ai.media import image_generation_service as mod
from app.services.ai.media.image_generation_service import ImageGenerationService
from app.services.media.parsers.video_providers import ImageGenResult

REF_A = "/tmp/refs/sheet.png"
REF_B = "/tmp/refs/worn.png"


class _StubProvider:
    """Records the kwargs the service hands the adapter.

    ``**kwargs`` mirrors the REAL ``BaseImageProvider.generate`` signature —
    every wired adapter takes it that way, which is also why passing a kwarg an
    adapter ignores cannot break it.
    """

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt, model, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, **kwargs})
        return ImageGenResult(image_url="https://cdn.test/1.png", provider="stub")


@pytest.mark.asyncio
async def test_reference_paths_reach_the_provider_on_the_registry_branch(monkeypatch):
    stub = _StubProvider()
    monkeypatch.setattr(mod.provider_registry, "get_image_provider", lambda name: stub)

    out = await ImageGenerationService().generate_image(
        project_id="asset",
        node_id="asset:5:sheet",
        prompt="a swordswoman, character sheet",
        model="stub-model",
        provider_name="stub",
        reference_image_url="https://app.test/api/v1/resources/9/cover",
        reference_image_paths=[REF_A, REF_B],
        aspect_ratio="1:1",
    )

    assert out["image_url"] == "https://cdn.test/1.png"
    call = stub.calls[0]
    assert call["reference_image_paths"] == [REF_A, REF_B]
    # The url still rides along for a future URL-based adapter, and the frame
    # the slot template asked for is not quietly replaced by the 16:9 default.
    assert call["reference_image_url"].endswith("/resources/9/cover")
    assert call["aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_reference_paths_reach_the_provider_on_the_db_catalog_branch(
    monkeypatch,
):
    """The branch production actually takes — the in-process image registry
    ships EMPTY, so every real call KeyErrors and resolves from the catalog."""
    stub = _StubProvider()

    def _registry_miss(name):
        raise KeyError(name)

    async def _resolve(provider_name, user_id=None):
        return stub, "doubao-seedream-4-0"

    monkeypatch.setattr(mod.provider_registry, "get_image_provider", _registry_miss)
    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        _resolve,
    )

    await ImageGenerationService().generate_image(
        project_id="asset",
        node_id="asset:5:sheet",
        prompt="a swordswoman",
        model="dall-e-3",  # the sentinel: yields to the catalog's actual_model
        provider_name=None,
        reference_image_paths=[REF_A],
    )

    call = stub.calls[0]
    assert call["reference_image_paths"] == [REF_A]
    assert call["model"] == "doubao-seedream-4-0"


@pytest.mark.asyncio
async def test_omitting_the_paths_sends_none_not_an_empty_list(monkeypatch):
    """The existing callers (shot generate / the agent GenerateImage tool) pass
    nothing; an adapter must see ``None``, the value its ``or []`` fallbacks are
    written against, rather than a falsy-but-present ``[]``."""
    stub = _StubProvider()
    monkeypatch.setattr(mod.provider_registry, "get_image_provider", lambda name: stub)

    await ImageGenerationService().generate_image(
        project_id="",
        node_id="1",
        prompt="p",
        model="m",
        provider_name="stub",
    )

    assert stub.calls[0]["reference_image_paths"] is None
