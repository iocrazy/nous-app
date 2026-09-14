"""OpenAI Images API (gpt-image-2.5) — the API-key path.

Same binary as the codex protocol, `--provider openai`. Unlike the subscription
path the API honours --size and the quality tiers `xhigh` / `max`, and the
image model is chosen by us (`actual_model` → `--model`). Billing is per token
on the operator's OpenAI account (the catalog row's api_key), which is why the
seeded display names carry "(OpenAI API)".

★ Provenance of the "honours exact sizes and xhigh/max" claim: OpenAI docs read
2026-09-13 (the gpt-image-2.5-flare / -sunburst model pages) plus the
gpt-image-2-skill 0.7.4 `--quality` enum. NOT yet measured against the live API
— that is Task 7 acceptance. Contrast `codex.py`, whose counterpart claim IS
measured (2026-09-09: that path rewrites `xhigh` to `medium` and says nothing),
and the difference between the two is the entire reason these are separate
protocols. Until the real-stack probe runs, treat this paragraph as a
documented expectation rather than an observation.
"""

from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    IMAGE_25_QUALITY_TIERS,
    ProviderCapabilities,
    ProviderNotConfiguredError,
    ProviderProtocol,
)
from app.services.ai.provider_protocols.codex import _CodexImageAdapter


class OpenAIImagesProtocol(ProviderProtocol):
    key = "openai-images"
    label = "OpenAI Images API (GPT Image 2.5)"
    description = (
        "gpt-image-2-skill CLI over the OpenAI Images API with the row's api_key "
        "(pay-as-you-go). actual_model is the image model: gpt-image-2.5-flare "
        "or gpt-image-2.5-sunburst. Honours exact sizes and xhigh/max quality "
        "(per docs 2026-09-13; not yet measured on the live API)."
    )
    model_types = ("image",)
    aliases = ()
    generation_family = "openai-images"
    supports_http_image_probe = False  # shells out to the CLI, like codex
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        quality_tiers=IMAGE_25_QUALITY_TIERS,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.codex_cli import (
            CodexCliProvider,
        )

        key = (row.get("api_key") or "").strip()
        actual_model = row.get("actual_model") or ""
        if not key:
            # A typed refusal at build time beats the CLI's `not_logged_in`
            # (which would read as a codex session problem, not a missing key).
            #
            # `ProviderNotConfiguredError`, NOT `ProtocolCapabilityError`: the
            # latter says "this protocol cannot do images", which is both
            # false and unactionable. The row is fine and the fix is one paste
            # by the person who just enabled it, so the refusal names the row
            # and the place — and carries it as `detail`, the field the
            # failure record shows the operator.
            raise ProviderNotConfiguredError(
                self.key,
                actual_model,
                detail=(f"{self.key} row has no api_key; set it in Admin > AI Models"),
            )
        return (
            _CodexImageAdapter(
                CodexCliProvider(provider_kind="openai", api_key=key),
                provider_name=self.key,
            ),
            actual_model,
        )
