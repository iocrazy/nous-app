"""Catalog model name → :class:`ProviderCapabilities`, and the one
"generates from a prompt" predicate.

``GET /canvases/generation-capabilities`` was the first consumer of this
lookup; the asset library's bundle endpoint is the second. Since spec
2026-09-25 §3.8 the capabilities endpoint keys its answer by the platform
provider view (``platform_provider.generation_picker_models``) — the same rows
the generation pickers map from — while the bundle still asks
:func:`visible_generation_rows` here. Both share :func:`generates_from_prompt`,
so an upscale-only row is excluded from both.

What lives here and what deliberately does not:

* **visibility for the bundle** (enabled + owner-scoped catalog rows + the
  user's Settings platform-model gate + image/video only);
* **the projection onto the wire** — NOT here. ``generation-capabilities``
  hides ``honours_ratio`` and ``actual_provider`` on purpose; the bundle emits
  only ``max_refs``. Each consumer picks what it publishes.

⚠️ ``include_actual_provider=True`` keeps upstream identity in the rows (the
2026-08-14 leak tripwire). Callers derive from it and never serialize it.
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

    The ONE predicate behind both :func:`visible_generation_rows` and
    ``platform_models[name].generatable``
    on the AI settings (``services/ai/platform_provider``), so the two cannot
    drift. False for every non-image/video row, and for upscale-only image
    services (nous-engine super-resolution: the protocol's ``text_to_image`` is
    False) — they need an input image and would fail on every prompt. A row
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


async def visible_generation_rows(
    user_id: str, *, include_actual_provider: bool = False
) -> List[Dict[str, Any]]:
    """The image/video catalog rows this user may see.

    Catalog + owner scope + the user's Settings card, image/video rows that
    generate from a prompt. The asset bundle reads it; the generation pickers
    and ``generation-capabilities`` read the platform provider view instead.
    """
    from app.repositories import nous_model_repository as _repo_mod
    from app.services.ai.platform_model_visibility import (
        filter_platform_models_for_user,
    )

    # actual_provider is always fetched — the upscale-only filter below needs
    # it — and stripped again unless the caller asked for it, so the default
    # projection stays byte-identical (the 2026-08-14 leak tripwire).
    rows = await _repo_mod.get_nous_model_repository().list_enabled(
        viewer_user_id=user_id, include_actual_provider=True
    )
    # The user's Settings → platform-model card (master switch + per-model
    # blacklist) applies here too; the picker must show what Settings shows.
    rows = await filter_platform_models_for_user(user_id, rows)
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not generates_from_prompt(r.get("type"), r.get("actual_provider")):
            continue
        if not include_actual_provider:
            r = {k: v for k, v in r.items() if k != "actual_provider"}
        out.append(r)
    return out


async def capabilities_for_model(
    model: str, user_id: str
) -> Optional[ProviderCapabilities]:
    """Capabilities of the catalog row named ``model``, or ``None``.

    ``None`` means **this caller cannot use that model** — it is disabled, it is
    another user's owner-scoped row, their Settings blacklist hides it, or no
    such name exists. All four are one fact from the caller's side ("not a model
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

    rows = await visible_generation_rows(user_id, include_actual_provider=True)
    # Exact name first, then the mediahub-/nous- rename alias.
    row = find_row_by_catalog_name(rows, wanted)
    if row is None:
        return None
    # The provider string is consumed HERE and never returned: only the
    # derived capability values leave this function.
    proto = resolve_generation_protocol((row.get("actual_provider") or "").lower())
    return proto.capabilities if proto else ProviderCapabilities.none()


__all__ = ["capabilities_for_model", "visible_generation_rows"]
