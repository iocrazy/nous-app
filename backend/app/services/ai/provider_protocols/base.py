"""Base class for provider protocols (Phase 2 — behavior-capable).

A concrete protocol subclasses this, sets the metadata class attributes, and
overrides only the build hook(s) its family supports. The build hooks import
their heavy adapter classes LAZILY (inside the method) so the registry can be
imported at startup without dragging in every adapter — and so factory can
import this package without a load-time cycle.
"""

from __future__ import annotations

from typing import Any, Optional


class ProtocolCapabilityError(RuntimeError):
    """A protocol was asked to build a capability it does not support
    (e.g. a chat-only protocol asked for an image provider)."""

    def __init__(self, key: str, capability: str) -> None:
        self.key = key
        self.capability = capability
        super().__init__(f"protocol {key!r} does not support {capability}")


class ProviderNotConfiguredError(ValueError):
    """No credential resolved for the provider serving ``model``.

    Raised instead of building a keyless adapter (opaque upstream 401) or —
    the pre-2026-07-07 behavior — silently falling back to env vars. Users
    configure their own keys in Settings → AI Providers; platform models are
    managed by the admin in Admin → AI Models."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(
            f"AI provider '{provider}' is not configured for model {model!r}. "
            "Add your API key in Settings → AI Providers, or ask the admin "
            "to enable a platform model (Admin → AI Models)."
        )


class ProviderProtocol:
    """One provider protocol. Metadata mirrors Phase 1's dataclass fields;
    build hooks own the per-protocol adapter/provider construction."""

    key: str = ""
    label: str = ""
    description: str = ""
    model_types: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    is_chat_key: bool = False
    generation_family: Optional[str] = None
    is_default: bool = False

    # ---- capability hooks (default: unsupported) --------------------
    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        """Build a chat/embedding/asr AIAdapter from a single-provider cred
        dict ``{"api_key": str, "base_url": str}``."""
        raise ProtocolCapabilityError(self.key, "chat")

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(BaseImageProvider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "image")

    def build_video_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(provider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "video")
