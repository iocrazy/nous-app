from __future__ import annotations

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    ProviderCapabilities,
    ProviderProtocol,
)


class JimengLocalProtocol(ProviderProtocol):
    """Image generation on the user's OWN machine via the paired nous-codex
    daemon driving the dreamina CLI. (The daemon can also do video; the
    routing order does not let a video row reach it yet — see ``model_types``.)

    ``jimeng-local`` has been a live ``mediahub_models.actual_provider`` value
    all along (``canvas_generation._LOCAL_ENGINES`` routes it to the daemon),
    but no protocol declared it — so it resolved to None and had no place to
    hang its capabilities.

    ``generation_family`` is deliberately NOT ``"jimeng-cli"``: the capability
    matrix is the same CLI's, but sharing the family would make
    ``db_registry._pick_row`` prefer these rows as "the jimeng row" and let
    ``resolve_video_provider`` build a SERVER-side JimengCliProvider for a
    model that is supposed to run on the user's machine. No build hooks for
    the same reason — dispatch goes through the daemon branch, and asking
    this protocol to build a provider raises ProtocolCapabilityError.
    """

    key = "jimeng-local"
    label = "Jimeng (Local daemon)"
    description = (
        "dreamina CLI on the user's paired device (OAuth session is the "
        "credential; nous never sees it). Image generation."
    )
    # Image only, though the daemon itself can do video: ``canvas_generation``
    # takes its ``kind == "video"`` branch — which resolves through
    # ``db_registry.resolve_video_provider``, and that rejects any family other
    # than "jimeng-cli" — and returns BEFORE it ever reaches the ``_local_engine``
    # check. So a jimeng-local video row is unreachable by construction: listing
    # "video" here would put an option in the admin dropdown that can only ever
    # raise. Widen this the same change that hoists the local-engine check above
    # the video branch, not before.
    model_types = ("image",)
    generation_family = "jimeng-local"
    # Same knobs as the server-side jimeng-cli protocol — it is the same CLI,
    # just executed on the user's machine (spec §3.2 groups them on one row).
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=False,
        resolution=True,
        # The image CLI is pure text2image — build_image_args takes no --image.
        # Video refs (first/last frame, multimodal) ride on video_modes instead.
        max_refs=0,
        negative=False,
        video_modes=frozenset({"frames", "multimodal"}),
        honours_ratio="native",
    )
