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

# Quality tiers are a VOCABULARY, not a bool: gpt-image-2.5 added `xhigh`
# and `max` and only the API-key path honours them (the codex subscription
# backend rewrote `xhigh` to `medium` when measured 2026-09-09). A protocol
# declares the tiers it can honour; reconcile drops the rest loudly.
LEGACY_QUALITY_TIERS: frozenset[str] = frozenset({"low", "medium", "high"})
IMAGE_25_QUALITY_TIERS: frozenset[str] = LEGACY_QUALITY_TIERS | {"xhigh", "max"}
# The ONE place the low→max ramp is written down. Every ordered projection
# derives from this; re-listing the strings anywhere else is the drift this
# constant exists to prevent.
QUALITY_TIER_ORDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


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

    def __init__(self, provider: str, model: str, detail: str = ""):
        self.provider = provider
        self.model = model
        # ``detail`` is read by ``describe_generation_failure`` (duck-typed on
        # ``code`` / ``detail``), which puts it in the task's
        # ``metadata.failure`` for the details pane. Optional because most
        # raise sites have nothing to add beyond the sentence below; a
        # provider whose remedy is more specific than "add a key somewhere"
        # says so here and that exact wording is what the record keeps.
        self.detail = detail
        super().__init__(
            detail
            or (
                f"AI provider '{provider}' is not configured for model {model!r}. "
                "Add your API key in Settings → AI Providers, or ask the admin "
                "to enable a platform model (Admin → AI Models)."
            )
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
    # Which tiers ``quality`` actually means. Empty whenever ``quality`` is
    # False — the bool says "this knob exists", the set says "and these are
    # the values it accepts", and a provider that honours the knob for only
    # some values can now say so instead of silently degrading the rest.
    quality_tiers: frozenset[str]
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
    quality_tiers=frozenset(),
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

    # Whose credential this protocol runs on — and therefore whose machine
    # executes it and whose quota pays for it. One of:
    #
    #   "api_key"        a key stored on the catalog row; runs on our servers
    #   "server_session" an OAuth session held by nous (no key on the row);
    #                    runs on our servers against a subscription quota
    #   "user_device"    the user's own credential on the user's own machine,
    #                    reached through the paired daemon; nous never sees it
    #   "endpoint"       a self-hosted server someone runs (Ollama, LM Studio):
    #                    the base_url is the credential and a key is optional.
    #                    Called by THIS process — not via the paired daemon, so
    #                    it is not "user_device" — and there is no vendor key
    #                    or quota, so it is not "api_key" either
    #
    # This is the ONLY thing separating the three gpt-image cards in Admin →
    # AI Models (codex / codex-local / openai-images) and the two dreamina
    # cards (jimeng-cli / jimeng-local). They drive the same binaries and
    # cannot be merged — sharing a generation_family would let db_registry
    # build a server-side provider for a row meant to run on the user's
    # machine — so the admin surface has to name the difference instead.
    #
    # Empty by default and rejected by test_protocol_credential_kind, so a new
    # protocol answers the question rather than inheriting a wrong answer.
    credential_kind: str = ""

    # The ``AIProvider`` subclass (app/services/ai/providers/ai_provider.py)
    # that serves this key, BY NAME — a string, not the class, so this module
    # never imports that one (the protocols are loaded at startup; the provider
    # module drags in the OpenAI SDK, and a real import here would also invert
    # the dependency that lets ai_provider derive its registry from us).
    #
    # ``AIProviderFactory`` resolves the name against its own module globals and
    # builds its registry from every protocol that sets this. Before 2026-09-22
    # that registry was a SECOND hand-written dict: adding ``nous`` to the
    # protocols did not add it there, and every transcription raised
    # ``Unknown provider: nous`` (PR #2375). Deriving it is why that can no
    # longer happen.
    #
    # Empty means "this protocol has no AIProvider" — legitimate for the
    # CLI/daemon families and for chat-only protocols the factory never serves.
    # Those keys must be listed in ``PROTOCOLS_WITHOUT_AI_PROVIDER``;
    # test_provider_registry_is_derived rejects a protocol that is neither
    # mapped nor exempt.
    ai_provider_name: str = ""

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

    # Two independent image axes, both properties of the implementation:
    #
    #   text_to_image   rows of this family may serve an ordinary prompt →
    #                   picture request (the canvas picker, the agent image
    #                   tool, resolve_image_provider's default pick).
    #   upscale_capable build_upscale_provider() returns an object with
    #                   ``upscale_image`` — the canvas 放大 route resolves
    #                   over these rows only.
    #
    # ``nous`` is the one family where they differ: its image rows are
    # nous-engine super-resolution services that REQUIRE an input image, so
    # listing them as text-to-image models would put an entry in the picker
    # that fails on every prompt.
    text_to_image: bool = True
    upscale_capable: bool = False

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

    def build_upscale_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(provider_with_upscale_image, actual_model)`` from a row.
        Only protocols with ``upscale_capable = True`` override this."""
        raise ProtocolCapabilityError(self.key, "upscale")

    def build_video_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(provider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "video")
