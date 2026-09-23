from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import (
    ProviderCapabilities,
    ProviderNotConfiguredError,
    ProviderProtocol,
)


class NousProtocol(ProviderProtocol):
    """The self-hosted nous-engine gateway (vLLM behind an OpenAI-compatible
    front door).

    Behaviourally this is the ``qwen`` protocol — both build an
    ``OpenAICompatibleAdapter`` over the row's base_url. It is a separate key
    because ``actual_provider`` is what Admin → AI Models shows on the card,
    and the engine's rows used to sit under ``openai`` ("OpenAI (native)"),
    which named a vendor we do not call and hid the one fact that matters
    about these rows: they run on our own hardware.

    Generic third-party OpenAI-compatible endpoints still belong on ``qwen``
    (label "OpenAI-Compatible (generic)"). The split is about provenance, not
    wire format: ``nous`` means we operate the machine.
    """

    key = "nous"
    label = "Nous Engine"
    description = (
        "Self-hosted nous-engine gateway (OpenAI-compatible /chat/completions; "
        "image rows are /images/generations upscale services). "
        "The base_url is part of the credential — the engine has no public "
        "default endpoint. Model deployment lives in nous-engine, not here."
    )
    model_types = ("llm", "embedding", "asr", "image")
    credential_kind = "api_key"
    ai_provider_name = "OpenAIProvider"
    is_chat_key = True

    # Image rows (``/images/generations``). Today every published nous-engine
    # image service is super-resolution (``studio-upscale``) and REQUIRES an
    # input image, so the family is upscale-only: its rows are kept out of the
    # text-to-image picker and default pick (``text_to_image = False``) and
    # served to the canvas 放大 route (``upscale_capable``). A future
    # text-to-image service needs its own family key (or a row-level flag) —
    # flipping ``text_to_image`` here would put the upscaler back in the
    # picker. See docs/runbook/nous-engine-image-bridge.md.
    generation_family = "nous"
    text_to_image = False
    upscale_capable = True
    # A health probe here would run a real (GPU-minutes) upscale — and would
    # need an input image the probe does not have. "not_probed" is the honest
    # answer; ``GET {base_url}/models?type=image`` is the authorisation probe.
    supports_http_image_probe = False
    # Declared, not inherited: none of the text-to-image knobs apply to an
    # upscaler, and saying so explicitly is what lets reconcile drop them
    # loudly instead of guessing.
    capabilities = ProviderCapabilities(
        ratios=frozenset(),
        quality=False,
        quality_tiers=frozenset(),
        resolution=False,
        max_refs=0,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="none",
    )

    def _images_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.nous_images import (
            NousImagesProvider,
        )

        actual_model = row.get("actual_model") or ""
        base_url = (row.get("base_url") or "").strip()
        api_key = (row.get("api_key") or "").strip()
        # Unlike chat, the image endpoint is behind InstanceApiKey auth — a
        # keyless row can only ever earn an opaque 401, so refuse it here and
        # name the row and the fix.
        missing = [
            n for n, v in (("base_url", base_url), ("api_key", api_key)) if not v
        ]
        if missing:
            raise ProviderNotConfiguredError(
                self.key,
                actual_model,
                detail=(
                    f"{self.key} image row {row.get('name')!r} has no "
                    f"{' / '.join(missing)}; set it in Admin > AI Models"
                ),
            )
        return (
            NousImagesProvider(
                base_url=base_url, api_key=api_key, default_model=actual_model
            ),
            actual_model,
        )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        return self._images_provider(row)

    def build_upscale_provider(self, row: dict[str, Any]) -> Any:
        return self._images_provider(row)

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.nous import NousAdapter

        # base_url, not api_key: a private gateway may legitimately run without
        # one, but it can never be reached without an address.
        base_url = (creds.get("base_url") or "").strip()
        if not base_url:
            raise ProviderNotConfiguredError("nous", model)
        return NousAdapter(
            api_url=base_url,
            api_key=(creds.get("api_key") or "").strip(),
            default_model=model,
        )
