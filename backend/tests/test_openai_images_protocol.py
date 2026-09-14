"""The ``openai-images`` protocol — gpt-image-2.5 through the API-key path.

Same CLI binary as ``codex``, a different upstream behind it: the public
Images API honours ``--size`` and the ``xhigh`` / ``max`` quality tiers, and
it bills the operator's OpenAI account rather than a ChatGPT subscription.
So the two protocols must stay separate rows in the registry — collapsing
them would hand ``xhigh`` to the backend that silently rewrites it.
"""

import pytest

from app.services.ai.provider_protocols import PROTOCOLS, resolve_generation_protocol
from app.services.ai.provider_protocols.base import ALL_RATIOS, IMAGE_25_QUALITY_TIERS


@pytest.mark.unit
def test_openai_images_is_registered_with_25_capabilities():
    p = resolve_generation_protocol("openai-images")
    assert p is not None and p.model_types == ("image",)
    caps = p.capabilities
    assert caps.ratios == ALL_RATIOS
    assert caps.quality and caps.quality_tiers == IMAGE_25_QUALITY_TIERS
    assert caps.resolution is True
    assert caps.honours_ratio == "native"
    assert caps.max_refs == 9 and caps.negative is False


@pytest.mark.unit
def test_build_image_provider_threads_key_and_model(monkeypatch):
    from app.services.ai.provider_protocols import openai_images as mod

    seen = {}

    class FakeCli:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.codex_cli.CodexCliProvider", FakeCli
    )
    proto = resolve_generation_protocol("openai-images")
    adapter, model = proto.build_image_provider(
        {"api_key": "sk-x", "actual_model": "gpt-image-2.5-sunburst"}
    )
    assert model == "gpt-image-2.5-sunburst"
    assert seen == {"provider_kind": "openai", "api_key": "sk-x"}


@pytest.mark.unit
def test_build_image_provider_refuses_empty_key():
    from app.services.ai.provider_protocols.base import ProtocolCapabilityError

    proto = resolve_generation_protocol("openai-images")
    with pytest.raises(ProtocolCapabilityError):
        proto.build_image_provider(
            {"api_key": "", "actual_model": "gpt-image-2.5-flare"}
        )


@pytest.mark.unit
def test_chat_keys_unchanged():
    from app.services.ai import provider_protocols as pp

    assert "openai-images" not in pp.chat_provider_keys()
