from __future__ import annotations

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    ProviderCapabilities,
    ProviderProtocol,
)


class JimengLocalProtocol(ProviderProtocol):
    """Image AND video generation on the user's OWN machine via the paired
    nous-codex daemon driving the dreamina CLI.

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
    # Both, now that ``canvas_generation`` asks "whose machine?" BEFORE it
    # splits on kind: a jimeng-local video row reaches the daemon arm instead
    # of falling into ``resolve_video_provider`` (which rejects any family
    # other than "jimeng-cli") and raising. This was image-only for exactly as
    # long as that video row was unroutable — listing an option the dropdown
    # could only ever fail on is worse than not listing it.
    model_types = ("image", "video")
    generation_family = "jimeng-local"
    # Same knobs as the server-side jimeng-cli protocol — it is the same CLI,
    # just executed on the user's machine (spec §3.2 groups them on one row).
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=False,
        resolution=True,
        # The image CLI is pure text2image — build_image_args takes no --image.
        # ``max_refs`` is read as an IMAGE cap only (see
        # ``GenerationRequest.reconcile``); video refs (first/last frame,
        # multimodal) are governed by ``video_modes`` below.
        max_refs=0,
        negative=False,
        video_modes=frozenset({"frames", "multimodal"}),
        honours_ratio="native",
    )
