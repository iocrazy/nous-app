"""Catalog model name → :class:`ProviderCapabilities`, and the one
"generates from a prompt" predicate.

``GET /canvases/generation-capabilities`` and the asset library's bundle
endpoint both key their answer by the platform provider view
(``platform_provider``: ``generation_picker_models`` / ``platform_rows`` with
purpose ``picker``) — the same rows the generation pickers map from — so a
model is looked up exactly when a picker could offer it. Both share
:func:`generates_from_prompt`, so an upscale-only row is excluded from both.

What does NOT live here: **the projection onto the wire**.
``generation-capabilities`` hides ``honours_ratio`` and ``actual_provider`` on
purpose; the bundle emits only ``max_refs``. Each consumer picks what it
publishes. The upstream provider is consumed here and never returned (the
2026-08-14 leak tripwire).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.catalog_names import find_row_by_catalog_name
from app.services.ai.provider_protocols.base import ProviderCapabilities

# Only these two catalog types can generate a picture or a clip; a chat row has
# no generation capabilities to speak of and must not appear in either
# consumer's answer.
_GENERATION_TYPES = ("image", "video")


def generates_from_prompt(
    row_type: Optional[str], actual_provider: Optional[str]
) -> bool:
    """Whether a catalog row can generate a picture or a clip from a prompt.

    The ONE predicate behind ``platform_models[name].generatable`` on the AI
    settings and the ``picker`` rows of ``platform_provider.platform_rows``,
    so the settings card, the pickers and the dispatch default cannot drift.
    False for every non-image/video row, and for upscale-only image services
    (nous-engine super-resolution: the protocol's ``text_to_image`` is False)
    — they need an input image and would fail on every prompt. A row
    whose protocol does not resolve stays True: "unknown protocol" is not
    "cannot generate", and dispatch answers that case with a typed refusal.
    """
    from app.services.ai.provider_protocols import resolve_generation_protocol

    if row_type not in _GENERATION_TYPES:
        return False
    proto = resolve_generation_protocol((actual_provider or "").lower())
    if row_type == "image" and proto is not None:
        return bool(getattr(proto, "text_to_image", True))
    return True


async def generation_rows_for(user_id: str) -> List[Dict[str, Any]]:
    """The image/video rows ``user_id``'s generation pickers offer, as full
    catalog rows (``actual_provider`` included — PRIVATE, never serialize).
    The platform provider view with purpose ``picker``; degrades to ``[]``
    (logged there) when it cannot be computed."""
    from app.services.ai.platform_provider import platform_rows

    rows = await platform_rows(user_id, purpose="picker")
    return [dict(r.catalog_row) for r in rows if r.model.type in _GENERATION_TYPES]


async def capabilities_for_model(
    model: str, user_id: str
) -> Optional[ProviderCapabilities]:
    """Capabilities of the catalog row named ``model``, or ``None``.

    ``None`` means **this caller cannot use that model** — it is disabled, it is
    another user's owner-scoped row, their Settings card hides it, governance
    is off, nous-engine no longer lists it, its probe failed, or no such name
    exists (``platform_rows(user_id, purpose="picker")``). All four are one fact from the caller's side ("not a model
    you can generate with"), and a caller turns it into a typed refusal.

    A model that IS visible but whose protocol does not resolve comes back as
    :meth:`ProviderCapabilities.none` — max_refs 0, every knob off — NOT as
    ``None``. The distinction is load-bearing: "you may not use this model" and
    "this provider accepts no references" are different answers, and collapsing
    them would report a provider that cannot take references as a typo.
    """
    wanted = str(model or "")
    if not wanted:
        return None
    from app.services.ai.provider_protocols import resolve_generation_protocol

    rows = await generation_rows_for(user_id)
    # Exact name first, then the mediahub-/nous- rename alias.
    row = find_row_by_catalog_name(rows, wanted)
    if row is None:
        return None
    # The provider string is consumed HERE and never returned: only the
    # derived capability values leave this function.
    proto = resolve_generation_protocol((row.get("actual_provider") or "").lower())
    return proto.capabilities if proto else ProviderCapabilities.none()


__all__ = ["capabilities_for_model", "generates_from_prompt", "generation_rows_for"]
