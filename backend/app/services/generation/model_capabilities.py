"""Catalog model name → :class:`ProviderCapabilities`, in ONE place.

``GET /canvases/generation-capabilities`` was the first consumer and owned this
lookup privately (``canvases_router._visible_generation_rows`` + a protocol
resolve). The asset library's bundle endpoint is the second, and it must answer
the same question the same way: a bundle trimmed to a ceiling the picker never
showed — or shown for a model the picker hides — is the "two predicates that
must agree" shape ``_visible_generation_rows``' own docstring was written to
prevent. So the predicate moved here and the router delegates; there is still
exactly one implementation.

What lives here and what deliberately does not:

* **visibility** (enabled + owner-scoped catalog rows + the user's Settings
  platform-model gate + image/video only) — here, because both consumers need
  the SAME row set;
* **the projection onto the wire** — NOT here. ``generation-capabilities``
  hides ``honours_ratio`` and ``actual_provider`` on purpose; the bundle emits
  only ``max_refs``. Each consumer picks what it publishes.

⚠️ ``include_actual_provider=True`` keeps upstream identity in the rows (the
2026-08-14 leak tripwire). Callers derive from it and never serialize it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.ai.provider_protocols.base import ProviderCapabilities

# Only these two catalog types can generate a picture or a clip; a chat row has
# no generation capabilities to speak of and must not appear in either
# consumer's answer.
_GENERATION_TYPES = ("image", "video")


async def visible_generation_rows(
    user_id: str, *, include_actual_provider: bool = False
) -> List[Dict[str, Any]]:
    """The image/video catalog rows this user may see.

    ``generation-models``, ``generation-capabilities`` and the asset bundle MUST
    agree row for row — a picker entry with no caps entry means the UI shows a
    knob it was told to hide, and a bundle for a model the picker hides means a
    reference trim nobody can explain.
    """
    from app.repositories import mediahub_model_repository as _repo_mod
    from app.services.ai.platform_model_visibility import (
        filter_platform_models_for_user,
    )

    rows = await _repo_mod.get_mediahub_model_repository().list_enabled(
        viewer_user_id=user_id, include_actual_provider=include_actual_provider
    )
    # The user's Settings → platform-model card (master switch + per-model
    # blacklist) applies here too; the picker must show what Settings shows.
    rows = await filter_platform_models_for_user(user_id, rows)
    return [r for r in rows if r.get("type") in _GENERATION_TYPES]


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

    for row in await visible_generation_rows(user_id, include_actual_provider=True):
        if str(row.get("name")) != wanted:
            continue
        # The provider string is consumed HERE and never returned: only the
        # derived capability values leave this function.
        proto = resolve_generation_protocol((row.get("actual_provider") or "").lower())
        return proto.capabilities if proto else ProviderCapabilities.none()
    return None


__all__ = ["capabilities_for_model", "visible_generation_rows"]
