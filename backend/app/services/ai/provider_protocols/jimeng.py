from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class JimengProtocol(ProviderProtocol):
    key = "jimeng-cli"
    label = "Jimeng CLI (即梦)"
    description = (
        "Subprocess dreamina CLI (OAuth session is the credential; no "
        "api_key). Primary image/video generator."
    )
    model_types = ("image", "video")
    aliases = ("jimeng",)
    generation_family = "jimeng-cli"
