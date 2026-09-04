"""Slot generation prompts + reference ordering (spec §5.1 "Generate missing").

Pure functions; no DB, no network, no provider. Everything here answers one
question — "given this asset, what exactly do we ask the image model for, and
which of its files ride along as references?" — so it can be read, diffed and
unit-tested without standing up a provider.

Two rules this module exists to keep honest:

* **a slot with no template is a typed refusal, not an empty prompt.** Sending
  a blank (or asset-prompt-only) request to a paid provider produces a
  plausible-looking image that has nothing to do with the slot, and nothing on
  the path would report that. ``SlotNotGeneratable`` is raised instead, and the
  service turns it into a 422 the user can act on.
* **composition order is fixed** (spec §6.3): asset ``prompt_positive`` →
  loadout ``prompt_extra`` → each linked costume/prop ``prompt_positive`` → the
  slot template. The template is deliberately LAST: it is the statement about
  framing/coverage, and a costume prompt landing after it would override the
  very thing the slot is for. Negatives are the UNION (asset ∪ template),
  deduped — a template negative must never delete the user's own.

Model Experience note: these strings are model-visible, but they are not part
of the system prompt — they ride as the ``prompt`` argument of one image
generation call, so they neither share nor invalidate the chat KV cache.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.services.assets.slots import PRIMARY_SLOT, SLOTS, UNSORTED


class SlotNotGeneratable(Exception):
    """This (asset_type, slot) has no generation template — by design.

    ``audio`` and ``prompt`` assets have nothing to draw; ``unsorted`` is "a
    file with no home" rather than a thing to produce; and a character's
    ``worn`` slot is filled by generating the COSTUME's ``worn`` (that template
    knows which garment is the subject).
    """

    def __init__(self, slot: str):
        self.slot = slot
        super().__init__(f"slot {slot!r} is not generatable")


# Negatives every template carries. Kept separate from the per-template set so
# a template only has to state what is specific to IT.
_BASE_NEGATIVE: tuple[str, ...] = (
    "text",
    "watermark",
    "signature",
    "logo",
    "lowres",
    "jpeg artifacts",
    "deformed hands",
    "extra limbs",
)

# (asset_type, slot) → (positive template, slot-specific negatives, aspect).
#
# The aspect is part of the TEMPLATE, not a caller knob: a costume flat lay
# ("front and back laid out side by side") and a 2x3 expression grid want
# different frames, and leaving both at the provider's 16:9 default crops the
# grid or wastes half the flat lay. The 2x3 expression grid is square, the
# flat lay is 3:2, everything else — including the four-across prop
# turnaround, which is a ROW and not a grid — is the cinematic default.
# Prose, deliberately: it is what the model reads. Assertions against it are
# tokenize-style (see the test module's docstring), so tuning a sentence
# churns one line rather than refreshing a snapshot.
_TEMPLATES: Dict[tuple[str, str], tuple[str, tuple[str, ...], str]] = {
    # ── character ──────────────────────────────────────────────────────────
    ("character", "sheet"): (
        "character sheet: one chest-up close-up on the left, and full-body "
        "front, side and back three views on the right, consistent identity "
        "and costume across every view, even studio lighting, neutral light "
        "grey background, full figure visible with no cropping",
        ("cropped", "multiple characters", "inconsistent face", "busy background"),
        "16:9",
    ),
    ("character", "expressions"): (
        "expression sheet: a 2x3 grid of six head-and-shoulders portraits of "
        "the same character — neutral, happy, angry, sad, surprised and "
        "thoughtful — identical framing, identical lighting and identical "
        "identity in every cell, neutral light grey background",
        ("cropped", "multiple characters", "inconsistent face", "busy background"),
        "1:1",
    ),
    ("character", "stills"): (
        "cinematic still of this character in an in-world setting, single "
        "subject, film lighting, shallow depth of field, natural pose",
        ("multiple characters", "inconsistent face", "studio backdrop"),
        "16:9",
    ),
    ("character", "extras"): (
        "reference detail shots of this character — hands, hair, accessories "
        "and footwear in close-up, consistent identity, plain background",
        ("full body shot", "busy background", "multiple characters"),
        "16:9",
    ),
    # ── location ───────────────────────────────────────────────────────────
    ("location", "establishing"): (
        "establishing wide shot of this location, full spatial context, "
        "natural depth, cinematic lighting, no people in frame",
        ("people", "characters", "cropped", "close-up"),
        "16:9",
    ),
    ("location", "keyframes"): (
        "the same place from the same vantage point at a different time of "
        "day, identical architecture and layout, only the light and mood "
        "change, cinematic lighting",
        ("people", "different location", "inconsistent geometry"),
        "16:9",
    ),
    ("location", "details"): (
        "close-up detail shots of this location — materials, textures, props "
        "and surfaces — consistent with the establishing shot",
        ("wide shot", "people", "different location"),
        "16:9",
    ),
    ("location", "layout"): (
        "top-down orthographic layout plan of this location, floor plan view, "
        "rooms and circulation labelled by shape, flat even lighting",
        ("perspective view", "people", "dramatic lighting"),
        "16:9",
    ),
    # ── prop ───────────────────────────────────────────────────────────────
    ("prop", "turnaround"): (
        "product turnaround of this prop: four angles — front, side, back and "
        "three-quarter — in one row, consistent scale and lighting across all "
        "four, neutral light grey background",
        ("multiple objects", "hands", "busy background", "cropped"),
        # WIDE, not square: "in one row" is four frames across. A 1:1 canvas
        # either crops the row or shrinks each angle to a quarter of the
        # height it needs — the exact miscrop the per-template aspect exists
        # to prevent. Only the 2x3 expression grid is genuinely square.
        "16:9",
    ),
    ("prop", "in_scene"): (
        "cinematic still of this prop in use inside its in-world setting, "
        "natural scale against its surroundings, film lighting",
        ("multiple objects", "studio backdrop"),
        "16:9",
    ),
    ("prop", "details"): (
        "close-up detail shots of this prop — materials, wear, markings and "
        "mechanism — consistent with the turnaround",
        ("wide shot", "busy background", "multiple objects"),
        "16:9",
    ),
    # ── costume ────────────────────────────────────────────────────────────
    ("costume", "flat"): (
        "flat lay of this costume, front and back laid out side by side, no "
        "body inside the garment, symmetrical arrangement, even overhead "
        "lighting, neutral light grey background",
        ("mannequin", "person", "body", "busy background", "cropped"),
        "3:2",
    ),
    ("costume", "worn"): (
        "this costume worn by a full-body figure, front three-quarter view, "
        "the garment reading clearly as the subject, even studio lighting, "
        "neutral light grey background",
        ("multiple characters", "cropped", "busy background"),
        "16:9",
    ),
    ("costume", "details"): (
        "close-up detail shots of this costume — fabric, seams, fastenings "
        "and trim — consistent with the flat lay",
        ("wide shot", "busy background", "full body shot"),
        "16:9",
    ),
}

# Negatives arrive as free text the user typed; split on the two separators a
# prompt field actually carries.
NEGATIVE_SPLIT = re.compile(r"[,\n]+")


def dedupe_fragments(fragments: Iterable[str]) -> List[str]:
    """Strip, drop blanks, drop case-insensitive repeats, keep first spelling."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in fragments:
        text = (raw or "").strip().strip(",").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def slot_prompt(
    asset_row: Dict[str, Any],
    slot: str,
    loadout_row: Optional[Dict[str, Any]] = None,
    linked_prompts: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """Compose ``{positive, negative, aspect_ratio}`` for one (asset, slot).

    ⚠️ ``negative`` is NOT a provider input: no image adapter in this repo
    accepts a negative prompt (``grep -rn negative`` over ``video_providers/``
    and ``provider_protocols/`` returns nothing). It is composed here and
    RECORDED as ``generated_media.params["negative"]`` — provenance the user
    can read and re-use, not something that shapes the image. Folding it into
    the positive text would be worse than not having it: "no watermark" inside
    a positive prompt is a request for a watermark on several models.

    ``linked_prompts`` are the ``prompt_positive`` values of the costumes and
    props this run dresses the asset in — resolved by the caller (from the
    loadout when one is given, from every ``wears``/``holds`` link otherwise),
    because THAT needs the database and this does not.

    Raises ``SlotNotGeneratable`` when the pair has no template.
    """
    asset_type = str(asset_row.get("asset_type") or "")
    template = _TEMPLATES.get((asset_type, slot))
    if template is None:
        raise SlotNotGeneratable(slot)
    body, template_negatives, aspect_ratio = template

    positive = ", ".join(
        dedupe_fragments(
            [
                asset_row.get("prompt_positive") or "",
                (loadout_row or {}).get("prompt_extra") or "",
                *[p or "" for p in (linked_prompts or [])],
                body,
            ]
        )
    )
    negative = ", ".join(
        dedupe_fragments(
            [
                *NEGATIVE_SPLIT.split(asset_row.get("prompt_negative") or ""),
                *template_negatives,
                *_BASE_NEGATIVE,
            ]
        )
    )
    return {
        "positive": positive,
        "negative": negative,
        "aspect_ratio": aspect_ratio,
    }


def _slot_priority(asset_type: str) -> List[str]:
    """Reference priority for a type: primary → worn → stills → the rest.

    Spec §6.3. ``worn`` and ``stills`` are named ahead of the declaration order
    because they carry the two things a generation most needs to stay
    consistent with — what the subject is wearing, and how it reads on camera.
    """
    if asset_type not in SLOTS:
        # Same posture as ``slots.readiness``: a typo'd type must fail loudly
        # rather than come back as a well-formed, plausible-looking [].
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    seq: List[str] = []
    primary = PRIMARY_SLOT.get(asset_type)
    if primary:
        seq.append(primary)
    for slot in ("worn", "stills", *SLOTS[asset_type], UNSORTED):
        if slot not in seq:
            seq.append(slot)
    return seq


def files_by_slot(
    files: List[Dict[str, Any]], loadout: Optional[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Group one asset's ``asset_files`` rows by slot, honouring the loadout.

    A loadout-scoped file belongs to ONE outfit. The prompt already treats the
    loadout as a filter, so letting a file pinned to a DIFFERENT loadout become
    a reference would make one plan describe two outfits — a costume in the
    picture that the prompt deliberately left out. With no loadout requested,
    only the unpinned files apply.

    Public and here rather than private to ``AssetsService`` because three
    surfaces now group the same rows (the slot plan, the canvas bundle, and a
    chat asset reference) and the rule is the one thing they must not disagree
    about: a second copy would let the primary image on the asset sheet differ
    from the one the model is told to fetch.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        pinned = f.get("loadout_id")
        if pinned is not None and (
            loadout is None or int(pinned) != int(loadout["id"])
        ):
            continue
        out.setdefault(f["slot"], []).append(f)
    return out


def reference_order(
    files_by_slot: Dict[str, List[Dict[str, Any]]],
    asset_type: str,
    max_refs: int,
) -> List[int]:
    """Pick the reference resource ids to send, in priority order, capped.

    ``max_refs`` is a PROVIDER limit (seedream-4 takes 3), so the overflow is
    dropped from the tail of the priority — the primary image is the one thing
    that must never be the one left out.

    The same resource attached to two slots consumes ONE reference slot: a
    provider handed the same image twice spends the cap without gaining
    information.
    """
    order = _slot_priority(asset_type)
    # A slot the priority list does not know about (a file left over from a
    # renamed slot) still gets its turn, last and in a deterministic order —
    # dropping it silently would be a reference the user attached and never
    # sees used.
    order += sorted(set(files_by_slot) - set(order))

    if max_refs <= 0:
        return []
    out: List[int] = []
    seen: set[int] = set()
    for slot in order:
        rows = files_by_slot.get(slot) or []
        # Stable sort: ties keep the caller's order (``list_files`` already
        # orders by slot, sort_order, attached_at).
        for row in sorted(rows, key=lambda r: r.get("sort_order") or 0):
            resource_id = int(row["resource_id"])
            if resource_id in seen:
                continue
            seen.add(resource_id)
            out.append(resource_id)
            if len(out) >= max_refs:
                return out
    return out


# ``dedupe_fragments`` / ``NEGATIVE_SPLIT`` are PUBLIC because a second
# module composes prompts from the same rows: ``assets/bundle.py`` reuses
# both so the composed prompt and the previewed prompt cannot drift apart
# on the dedupe rule or on what counts as a negative fragment. They were
# underscore-private and imported across the boundary anyway, which said
# the opposite of what the arrangement actually is.
# ``files_by_slot`` is public for the same reason: three callers, one rule.
__all__ = [
    "NEGATIVE_SPLIT",
    "SlotNotGeneratable",
    "dedupe_fragments",
    "files_by_slot",
    "reference_order",
    "slot_prompt",
]
