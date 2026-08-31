"""Base class for provider protocols (Phase 2 — behavior-capable).

A concrete protocol subclasses this, sets the metadata class attributes, and
overrides only the build hook(s) its family supports. The build hooks import
their heavy adapter classes LAZILY (inside the method) so the registry can be
imported at startup without dragging in every adapter — and so factory can
import this package without a load-time cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.services.generation.aspect import ASPECT_RATIOS

# Derived, never re-typed: the canvas vocabulary lives in aspect.py, and a
# second literal here is exactly the drift this contract exists to end.
ALL_RATIOS: frozenset[str] = frozenset(ASPECT_RATIOS)


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


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a generation provider can actually honour.

    Declared in code next to the implementation — a capability is a property
    of the code, not configuration; putting it in the catalog table would
    invent a third place that can disagree with the other two.
    """

    ratios: frozenset[str]
    quality: bool
    resolution: bool
    max_refs: int
    negative: bool
    video_modes: frozenset[str]
    honours_ratio: Literal["native", "prompt_hint", "none"]

    @classmethod
    def none(cls) -> "ProviderCapabilities":
        """The restrictive default: supports nothing. A protocol that forgets to
        declare drops every knob loudly (dropped_knobs) instead of ignoring
        them quietly — the failure mode this whole contract exists to end.

        Returns the module-level singleton so callers can use ``is`` to ask
        "did this protocol actually declare anything?" — an equal-but-distinct
        instance would answer that question wrong."""
        return _NONE


_NONE = ProviderCapabilities(
    ratios=frozenset(),
    quality=False,
    resolution=False,
    max_refs=0,
    negative=False,
    video_modes=frozenset(),
    honours_ratio="none",
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

    # Whether image generation for this family is an HTTP call made BY THIS
    # PROCESS — the only shape a server-side health probe can reach.
    #
    # False for every CLI/daemon-backed family: ``codex`` and ``jimeng-cli``
    # shell out to a local binary, ``codex-local`` dials the user's own paired
    # device. Their catalog rows carry an empty ``base_url`` precisely because
    # there is no endpoint, so a probe would not be "failing" — it would be
    # inapplicable, which is what ``not_probed`` already means.
    #
    # Declared per protocol rather than inferred from ``base_url`` being
    # non-empty: a row can be misconfigured with a stray base_url, and the
    # question "can this family be probed at all" is a property of the
    # implementation, not of one row. test_image_model_probe.py enumerates
    # every generation protocol against an expected mapping, so adding an
    # image family forces an explicit answer instead of inheriting False.
    supports_http_image_probe: bool = False
    # Generation protocols override this. The default supports nothing, so a
    # protocol that forgets to declare fails loudly rather than promising
    # knobs it will silently discard.
    capabilities: ProviderCapabilities = _NONE

    # ---- capability hooks (default: unsupported) --------------------
    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        """Build a chat/embedding/asr AIAdapter from a single-provider cred
        dict ``{"api_key": str, "base_url": str}``.

        ``context`` carries call-site facts that are NOT credentials. Today the
        only key is ``user_id``, consumed solely by protocols that route
        per-user (``codex-local`` dials THAT user's paired daemon). Every other
        protocol accepts and ignores it — the factory passes it unconditionally,
        so a subclass that omits ``**context`` would TypeError on every build."""
        raise ProtocolCapabilityError(self.key, "chat")

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(BaseImageProvider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "image")

    def build_video_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(provider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "video")
