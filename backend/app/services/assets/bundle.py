"""The bundle delivery protocol (spec §6.3) — what an asset hands a generator.

One pure function. Given an asset, the outfit picked for it, the costumes /
props / locations it is dressed in, the files attached to it, and the
CAPABILITIES OF THE PROVIDER THAT WILL RUN, it answers with the prompt to send,
the references that fit, and — the part this module exists for — the references
that did NOT fit, each with a reason.

Two things separate it from :mod:`slot_generation`, which it otherwise reuses
wholesale:

* **no slot template.** ``slot_prompt`` composes a request for one specific
  picture ("character sheet: one chest-up close-up on the left…"); a bundle is
  the asset's own description handed to whatever the canvas is generating. The
  template would overwrite the caller's intent, so it is absent and the user's
  own text takes its place at the tail.
* **the reference ceiling is the PROVIDER's, not a constant.**
  ``MAX_SLOT_REFERENCES = 3`` in ``assets_service`` is a hand-picked lowest
  common denominator (its own comment says so) — fine for a fixed internal
  path, wrong for a delivery protocol, because the same asset delivered to
  codex (9) and to ark (0) must trim differently. ``caps.max_refs`` is the
  single source of truth (``provider_protocols/base.py`` forbids a second one),
  so a bundle for a text-to-image-only provider drops EVERY reference — and
  says so, rather than shipping a payload the provider will ignore.

**Nothing is dropped silently.** Every candidate reference either comes back in
``reference_resource_ids`` or appears in ``dropped`` with one of three reasons:

``no_image_file``
    The attached resource has no image bytes to send (a document, an audio
    file, a video with no derived cover). Decided by the caller — that needs the
    ``resources`` rows — and passed in as ``has_image=False`` on the file row.
``over_limit``
    It lost the priority contest: the provider takes N, and this one ranked
    past N. Tail-dropped, so the primary image is never the casualty.
``provider_no_refs``
    ``caps.max_refs == 0``. Its OWN reason rather than ``over_limit`` for a
    limit of zero, because the user's fix is different: "pick fewer references"
    versus "pick a different model". Reporting these as ``over_limit`` would
    send someone unpicking references that were never going to be sent.

Model Experience note: ``positive`` is model-visible text, but it rides as the
``prompt`` argument of one generation call — it neither shares nor invalidates
any chat KV cache. ``negative`` is RECORDED PROVENANCE and reaches no provider
at all (no image adapter in this repo accepts a negative prompt); the same
caveat ``slot_prompt`` carries applies here unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.services.ai.provider_protocols.base import ProviderCapabilities

# Reused, not re-implemented: the case-insensitive first-spelling-wins dedupe
# and the "a negative field is free text split on , and newline" rule are the
# same rules on both surfaces, and a second copy is how the composed prompt and
# the previewed prompt quietly stop agreeing.
from app.services.assets.slot_generation import (
    _NEGATIVE_SPLIT,
    _dedupe,
    reference_order,
)

# Ordering rule for the linked assets' prompts, spec §6.3: what the subject
# WEARS and CARRIES first (those describe the subject), the place last (that
# describes the frame around it). ``sorted`` inside each bucket is not applied —
# the caller's order is meaningful (a loadout stores its costumes in the order
# the user arranged them) and is preserved.
_LINKED_PROMPT_ORDER = ("costume", "prop", "location")

DroppedReference = Dict[str, str]


def _linked_positive_texts(linked_assets: Sequence[Dict[str, Any]]) -> List[str]:
    """``prompt_positive`` of the linked assets, costumes/props before location.

    ⚠️ **No link relation produces a location today.** ``LINK_RULES`` allows
    ``wears`` (character→costume), ``holds`` (character→prop), ``ambience_of``
    and ``voice_of`` (audio→…) and nothing else, so a caller reading
    ``asset_links`` cannot hand one over. The branch is here because §6.3 states
    the ORDER and this is where order is decided — a later caller (a canvas node
    that pairs a character with a scene) inherits the rule instead of inventing
    a second one. It is exercised by the unit tests, not by any service path;
    do not read its presence as "locations are linkable".

    An asset type outside the three is ignored rather than appended: an audio
    asset's prompt has nothing to say to an image model, and quietly widening
    this to "everything else, last" would put it there.
    """
    by_type: Dict[str, List[str]] = {k: [] for k in _LINKED_PROMPT_ORDER}
    for row in linked_assets or []:
        bucket = by_type.get(str(row.get("asset_type") or ""))
        if bucket is None:
            continue
        text = (row.get("prompt_positive") or "").strip()
        if text:
            bucket.append(text)
    out: List[str] = []
    for kind in _LINKED_PROMPT_ORDER:
        out.extend(by_type[kind])
    return out


def _linked_negative_texts(linked_assets: Sequence[Dict[str, Any]]) -> List[str]:
    """Every linked asset's ``prompt_negative``, split into fragments.

    Same bucket rule as the positives, same reason.
    """
    out: List[str] = []
    for kind in _LINKED_PROMPT_ORDER:
        for row in linked_assets or []:
            if str(row.get("asset_type") or "") != kind:
                continue
            out.extend(_NEGATIVE_SPLIT.split(row.get("prompt_negative") or ""))
    return out


def _partition_files(
    files_by_slot: Dict[str, List[Dict[str, Any]]],
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Split the slot map into (files with image bytes, files without).

    ``has_image`` is an OPTIONAL key the caller stamps on the ``asset_files``
    row: absent means "not looked up", which is treated as a candidate. Only an
    explicit ``False`` disqualifies a file — a caller that cannot resolve media
    rows must not have every reference silently vanish.
    """
    keep: Dict[str, List[Dict[str, Any]]] = {}
    drop: Dict[str, List[Dict[str, Any]]] = {}
    for slot, rows in (files_by_slot or {}).items():
        for row in rows or []:
            target = drop if row.get("has_image") is False else keep
            target.setdefault(slot, []).append(row)
    return keep, drop


def _all_in_priority_order(
    files_by_slot: Dict[str, List[Dict[str, Any]]], asset_type: str
) -> List[int]:
    """Every resource in the map, deduped, in reference priority order.

    ``reference_order`` with a cap that cannot bind: the SAME ordering and the
    SAME dedupe the capped call uses, so "which one is over the limit" is
    decided by the ranking that actually ships, not by a second sort written
    here.
    """
    total = sum(len(rows or []) for rows in (files_by_slot or {}).values())
    if total <= 0:
        return []
    return reference_order(files_by_slot, asset_type, max_refs=total)


def build_bundle(
    asset_row: Dict[str, Any],
    loadout_row: Optional[Dict[str, Any]],
    linked_assets: Sequence[Dict[str, Any]],
    files_by_slot: Dict[str, List[Dict[str, Any]]],
    caps: ProviderCapabilities,
    user_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Compose one asset's delivery payload for one provider.

    Positive composition order is fixed (spec §6.3) and each piece earns its
    place in it:

    1. ``asset_row.prompt_positive`` — who or what this is;
    2. ``loadout_row.prompt_extra`` — the picked outfit's own note;
    3. each linked costume then prop ``prompt_positive`` — what it is wearing
       and carrying, the loadout having already FILTERED which ones (that is
       the caller's job — see ``AssetsService._linked_prompts``);
    4. a linked location's ``prompt_positive`` — where it is;
    5. ``user_text`` — what the person typed on the node, LAST so it can steer
       everything above it. A generator reads the tail as the most specific
       instruction, which is exactly the standing an ad-hoc request should have.

    ``negative`` is the deduped UNION of the asset's own negatives and every
    linked asset's. The base negatives ``slot_prompt`` adds ("text",
    "watermark", …) are deliberately NOT here: they belong to the slot
    templates, which a bundle does not use, and inventing them would put words
    into a payload the caller is about to hand to a model of their choosing.

    ``max_refs`` is echoed so the caller can render "3 of 5 sent" without
    re-deriving the ceiling from the two list lengths — which would read
    ``max_refs`` as 3 for an asset that only has 3 files.
    """
    asset_type = str(asset_row.get("asset_type") or "")
    linked_positive = _linked_positive_texts(linked_assets)

    positive = ", ".join(
        _dedupe(
            [
                asset_row.get("prompt_positive") or "",
                (loadout_row or {}).get("prompt_extra") or "",
                *linked_positive,
                (user_text or "").strip(),
            ]
        )
    )
    negative = ", ".join(
        _dedupe(
            [
                *_NEGATIVE_SPLIT.split(asset_row.get("prompt_negative") or ""),
                *_linked_negative_texts(linked_assets),
            ]
        )
    )

    with_image, without_image = _partition_files(files_by_slot)
    dropped: List[DroppedReference] = [
        {"resource_id": str(rid), "reason": "no_image_file"}
        for rid in _all_in_priority_order(without_image, asset_type)
    ]

    ordered = _all_in_priority_order(with_image, asset_type)
    limit = max(0, int(caps.max_refs))
    selected, overflow = ordered[:limit], ordered[limit:]
    # Zero is not "everything overflowed": the provider takes no references at
    # all, and the user's remedy is a different model, not fewer picks.
    overflow_reason = "provider_no_refs" if limit == 0 else "over_limit"
    dropped.extend(
        {"resource_id": str(rid), "reason": overflow_reason} for rid in overflow
    )

    return {
        "prompt": {"positive": positive, "negative": negative},
        "reference_resource_ids": [str(rid) for rid in selected],
        "dropped": dropped,
        "max_refs": limit,
    }


__all__ = ["build_bundle"]
