"""The ``openai-images`` protocol — gpt-image-2.5 through the API-key path.

Same CLI binary as ``codex``, a different upstream behind it: the public
Images API honours ``--size`` and the ``xhigh`` / ``max`` quality tiers, and
it bills the operator's OpenAI account rather than a ChatGPT subscription.
So the two protocols must stay separate rows in the registry — collapsing
them would hand ``xhigh`` to the backend that silently rewrites it.
"""

import pytest

from app.services.ai.provider_protocols import resolve_generation_protocol
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


class _FakeGenResult:
    """What ``CodexCliProvider.generate_image`` hands back."""

    local_path = "/tmp/gen.png"
    mime = "image/png"
    raw: dict = {}


def _fake_cli(monkeypatch, seen: dict):
    """Patch the CLI class at the import site ``build_image_provider`` uses.

    The import is lazy (inside the method), so patching the attribute on
    ``codex_cli`` is what the protocol actually reads.
    """

    class FakeCli:
        def __init__(self, **kw):
            seen.update(kw)

        async def generate_image(self, **kw):
            return _FakeGenResult()

    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.codex_cli.CodexCliProvider", FakeCli
    )


@pytest.mark.unit
def test_build_image_provider_threads_key_and_model(monkeypatch):
    seen = {}
    _fake_cli(monkeypatch, seen)

    proto = resolve_generation_protocol("openai-images")
    adapter, model = proto.build_image_provider(
        {"api_key": "sk-x", "actual_model": "gpt-image-2.5-sunburst"}
    )
    assert model == "gpt-image-2.5-sunburst"
    assert seen == {"provider_kind": "openai", "api_key": "sk-x"}


@pytest.mark.unit
async def test_generated_images_are_attributed_to_this_protocol(monkeypatch):
    """The result calls itself ``openai-images``, not ``codex``.

    Asserted through the public ``ImageGenResult.provider`` rather than the
    adapter's private field, because that string is what ``canvas_generation``
    copies into the generated-media record. Two protocols drive the same
    binary and the difference between them is which account gets billed, so
    an image filed under the wrong one is a real accounting error that
    nothing else in the pipeline would flag — ``provider_key`` is a separate
    field and would still be right.
    """
    _fake_cli(monkeypatch, {})

    proto = resolve_generation_protocol("openai-images")
    adapter, model = proto.build_image_provider(
        {"api_key": "sk-x", "actual_model": "gpt-image-2.5-flare"}
    )
    result = await adapter.generate("a fox", model)

    assert result.provider == "openai-images"


@pytest.mark.unit
def test_build_image_provider_refuses_empty_key():
    """An unpasted key is a CONFIGURATION refusal, not a protocol defect.

    ``ProtocolCapabilityError`` renders as "protocol 'openai-images' does not
    support image (api_key missing)" — which reads as "this protocol is
    broken" to the one person who can fix it in ten seconds. The rows ship
    disabled, so this fires exactly when an operator enables one before
    pasting the key: foreseeable, and the message is the whole remedy.
    """
    from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

    proto = resolve_generation_protocol("openai-images")
    with pytest.raises(ProviderNotConfiguredError) as ei:
        proto.build_image_provider(
            {"api_key": "", "actual_model": "gpt-image-2.5-flare"}
        )
    assert ei.value.provider == "openai-images"
    assert ei.value.model == "gpt-image-2.5-flare"
    assert "api_key" in ei.value.detail and "Admin" in ei.value.detail


@pytest.mark.unit
def test_the_refusal_detail_is_what_the_outcome_would_record():
    """The detail is not decoration — it is the field the failure record reads.

    ``describe_generation_failure`` is duck-typed on ``code`` / ``detail``
    (that is how the daemon and in-container codex paths share one
    description), and ``_record_failure_detail`` puts ``detail`` into the
    task's ``metadata.failure`` for the details pane. An exception without one
    lands there as an empty string and the pane has nothing to show.

    ⚠️ Today ``resolve_image_provider`` is called OUTSIDE the ``try`` in
    ``canvas_generation._generate_image``, so this refusal still propagates as
    a raise rather than reaching ``_record_failure_detail``. Widening that
    ``try`` was ruled a larger change than the fix wave; this test pins the
    contract so the detail is already right when it does.
    """
    from app.services.generation.failure import describe_generation_failure

    proto = resolve_generation_protocol("openai-images")
    with pytest.raises(Exception) as ei:
        proto.build_image_provider({"api_key": "   ", "actual_model": "x"})

    _message, patch = describe_generation_failure(ei.value)
    assert patch["failure"]["detail"] == (
        "openai-images row has no api_key — set it in Admin → AI Models"
    )


@pytest.mark.unit
def test_chat_keys_unchanged():
    from app.services.ai import provider_protocols as pp

    assert "openai-images" not in pp.chat_provider_keys()
