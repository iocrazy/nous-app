"""Slot table + readiness (spec §3.6) and link-type rules (spec §3.3).

Pure functions; no DB. Slots are code constants on purpose — users cannot
define slots in v1 (decision 15). readiness is DERIVED, never stored.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# type → primary slot (drives readiness). prompt has no file slot: its body is
# the "primary".
PRIMARY_SLOT: dict[str, str | None] = {
    "character": "sheet",
    "location": "establishing",
    "prop": "turnaround",
    "costume": "flat",
    "prompt": None,
    "audio": "primary",
}

# type → every named slot (primary first). 'unsorted' is implicit for all.
SLOTS: dict[str, tuple[str, ...]] = {
    "character": ("sheet", "stills", "expressions", "extras", "worn"),
    "location": ("establishing", "keyframes", "details", "layout"),
    "prop": ("turnaround", "in_scene", "details"),
    "costume": ("flat", "worn", "details"),
    "prompt": ("examples",),
    "audio": ("primary", "variants"),
}

UNSORTED = "unsorted"


def is_valid_slot(asset_type: str, slot: str) -> bool:
    slots = SLOTS.get(asset_type)
    if slots is None:
        return False
    return slot == UNSORTED or slot in slots


class Readiness(TypedDict):
    state: Literal["ready", "draft"]
    missing: list[str]


def readiness(
    asset_type: str, slot_counts: dict[str, int], prompt_positive: str | None
) -> Readiness:
    """ready iff the primary slot has ≥1 file (prompt: non-blank body)."""
    primary = PRIMARY_SLOT.get(asset_type)
    if asset_type == "prompt":
        ok = bool(prompt_positive and prompt_positive.strip())
        return {
            "state": "ready" if ok else "draft",
            "missing": [] if ok else ["prompt_positive"],
        }
    if primary is None:
        return {"state": "draft", "missing": []}
    ok = slot_counts.get(primary, 0) > 0
    return {"state": "ready" if ok else "draft", "missing": [] if ok else [primary]}


# relation → (from_type, to_type). audio subtypes are checked in link_allowed.
LINK_RULES: dict[str, tuple[str, str]] = {
    "wears": ("character", "costume"),
    "holds": ("character", "prop"),
    "ambience_of": ("audio", "location"),
    "voice_of": ("audio", "character"),
}

_AUDIO_SUBTYPE_FOR_RELATION: dict[str, frozenset[str]] = {
    "ambience_of": frozenset({"sfx", "music"}),
    "voice_of": frozenset({"voice"}),
}


def link_allowed(
    relation: str, from_type: str, from_subtype: str | None, to_type: str
) -> bool:
    rule = LINK_RULES.get(relation)
    if rule is None or rule != (from_type, to_type):
        return False
    allowed_sub = _AUDIO_SUBTYPE_FOR_RELATION.get(relation)
    if allowed_sub is not None and (from_subtype or "") not in allowed_sub:
        return False
    return True
