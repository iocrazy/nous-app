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

**The candidate population is the caller's SELECTION when it gives one.**
``selected_resource_ids`` narrows the file map before anything is ranked, so the
provider's ceiling trims the top N *of what the user ticked*, not the top N of
everything the asset owns intersected with what they ticked. Those two are not
the same answer, and the difference is not academic: with six files, a ceiling
of three and a single tick on the file ranked fourth, "trim first" delivers
NOTHING while reporting the user's own pick as ``over_limit`` — the card says
the provider's limit was the problem when the real cause was the order of the
two steps. ``None`` means "no selection given" and every file is a candidate,
which is what the asset sheet asks for; an EMPTY sequence means "the user
ticked nothing" and is honoured as such (no references, and nothing dropped —
nothing was chosen to drop).

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
    NEGATIVE_SPLIT,
    dedupe_fragments,
    reference_order,
)

# Ordering rule for the linked assets' prompts, spec §6.3: what the subject
# WEARS and CARRIES first (those describe the subject), the place last (that
# describes the frame around it). ``sorted`` inside each bucket is not applied —
# the caller's order is meaningful (a loadout stores its costumes in the order
# the user arranged them) and is preserved.
_LINKED_PROMPT_ORDER = ("costume", "prop", "location")

DroppedReference = Dict[str, str]


def linked_positive_texts(linked_assets: Sequence[Dict[str, Any]]) -> List[str]:
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
            out.extend(NEGATIVE_SPLIT.split(row.get("prompt_negative") or ""))
    return out


def partition_files_by_image(
    files_by_slot: Dict[str, List[Dict[str, Any]]],
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Split the slot map into (files with image bytes, files without).

    ``has_image`` is an OPTIONAL key the caller stamps on the ``asset_files``
    row: absent means "not looked up", which is treated as a candidate. Only an
    explicit ``False`` disqualifies a file — a caller that cannot resolve media
    rows must not have every reference silently vanish.

    PUBLIC because ``assets/chat_ref.py`` must apply the SAME rule before it
    ranks: picking the top-priority file first and checking its ``has_image``
    afterwards answers a different question — on a slot map whose highest
    priority file has no bytes, that reports "this asset has no image" while
    a usable one sits in the next slot down. The two surfaces must agree on
    which file IS the asset's picture.
    """
    keep: Dict[str, List[Dict[str, Any]]] = {}
    drop: Dict[str, List[Dict[str, Any]]] = {}
    for slot, rows in (files_by_slot or {}).items():
        for row in rows or []:
            target = drop if row.get("has_image") is False else keep
            target.setdefault(slot, []).append(row)
    return keep, drop


def _restrict_to_selection(
    files_by_slot: Dict[str, List[Dict[str, Any]]],
    selected_resource_ids: Optional[Sequence[Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """The slot map narrowed to the resources the caller ticked.

    Applied BEFORE ranking and trimming, which is the whole point — see the
    module docstring. ``None`` passes the map through untouched (no selection
    was given); anything else is honoured literally, including an empty
    sequence.

    Comparison is on ``str(resource_id)`` because the two sides arrive in
    different shapes: the file rows carry the DB's ``int`` Snowflake, the
    selection arrives off the wire as the string the client holds in
    ``selected_file_ids``. Comparing them raw is the "index built on strings,
    response gives numbers" mismatch this repo already paid for once.

    An id in the selection that names no file of this asset simply matches
    nothing. It is neither delivered nor reported — there is no file to report
    ON, and the sheet's own checklist cannot draw a row for it either. That is
    the disclosed stale-selection gap (a loadout changed before the detail
    loaded), not a silent drop of something deliverable.

    Empty slot lists are not carried over: an empty map and a map of empty
    lists must be the same input to the ranker.
    """
    if selected_resource_ids is None:
        return files_by_slot or {}
    keep = {str(rid) for rid in selected_resource_ids}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for slot, rows in (files_by_slot or {}).items():
        picked = [r for r in (rows or []) if str(r.get("resource_id")) in keep]
        if picked:
            out[slot] = picked
    return out


def _all_in_priority_order(
    files_by_slot: Dict[str, List[Dict[str, Any]]], asset_type: str
) -> List[int]:
    """Every resource in the map, deduped, in reference priority order.

    ``reference_order`` with a cap that cannot bind: the SAME ordering and the
    SAME dedupe the capped call uses, so "which one is over the limit" is
    decided by the ranking that actually ships, not by a second sort written
    here.

    ⚠️ The empty short circuit returns BEFORE ``reference_order`` can reject an
    unknown ``asset_type``, so a bundle for a type outside ``SLOTS`` with no
    files composes cleanly instead of raising — the opposite of the posture
    ``test_an_unknown_asset_type_fails_loudly`` pins for the same type WITH
    files. Unreachable through the service (the column is CHECK-constrained and
    every one of the six types is in ``SLOTS``), and left as is deliberately:
    moving the validation earlier would buy a raise on a path no caller can
    reach, at the cost of a lookup on the path every caller does.
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
    selected_resource_ids: Optional[Sequence[Any]] = None,
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

    ``selected_resource_ids`` is the caller's checklist and it bounds the WHOLE
    answer: both lists — delivered and dropped — are drawn from it alone. A
    caller that ticked three files and had all three sent therefore gets an
    EMPTY ``dropped``, rather than a report about files it never asked for. See
    ``_restrict_to_selection`` and the module docstring for why the narrowing
    happens before the ranking rather than after it.
    """
    asset_type = str(asset_row.get("asset_type") or "")
    linked_positive = linked_positive_texts(linked_assets)

    positive = ", ".join(
        dedupe_fragments(
            [
                asset_row.get("prompt_positive") or "",
                (loadout_row or {}).get("prompt_extra") or "",
                *linked_positive,
                (user_text or "").strip(),
            ]
        )
    )
    negative = ", ".join(
        dedupe_fragments(
            [
                *NEGATIVE_SPLIT.split(asset_row.get("prompt_negative") or ""),
                *_linked_negative_texts(linked_assets),
            ]
        )
    )

    candidates = _restrict_to_selection(files_by_slot, selected_resource_ids)
    with_image, without_image = partition_files_by_image(candidates)
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


# ``linked_positive_texts`` is PUBLIC because a second surface composes from
# the same rows: ``assets/chat_ref.py`` reuses it so an asset delivered to a
# generator and the same asset mentioned in chat cannot start disagreeing
# about the order its costumes/props/location are described in.
__all__ = ["build_bundle", "linked_positive_texts", "partition_files_by_image"]
