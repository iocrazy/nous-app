from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ArkProtocol(ProviderProtocol):
    key = "ark"
    label = "Ark image/video (方舟)"
    description = "Volcengine Ark task protocol for image/video generation."
    model_types = ("image", "video")
    aliases = ("doubao",)
    generation_family = "ark"
