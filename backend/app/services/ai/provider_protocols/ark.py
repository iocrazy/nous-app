from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import (
    ProviderCapabilities,
    ProviderProtocol,
)


class ArkProtocol(ProviderProtocol):
    key = "ark"
    label = "Ark image/video (方舟)"
    description = "Volcengine Ark task protocol for image/video generation."
    model_types = ("image", "video")
    aliases = ("doubao",)
    generation_family = "ark"
    capabilities = ProviderCapabilities(
        # Exactly ark_image._ASPECT_TO_SIZE's keys — five, not eight.
        ratios=frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"}),
        quality=False,
        resolution=False,
        max_refs=0,  # /images/generations is pure text-to-image
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.ark_image import (
            ArkImageProvider,
        )

        actual_model = row.get("actual_model") or ""
        provider = ArkImageProvider(
            api_key=row.get("api_key") or "",
            base_url=row.get("base_url") or "",
            default_model=actual_model,
        )
        return provider, actual_model
