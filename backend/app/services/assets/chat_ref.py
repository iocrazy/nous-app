"""One asset, expanded for a CHAT turn: primary image + consistency prompt.

Pure functions, no DB and no ``ProviderCapabilities``. This is the chat-side
sibling of :mod:`app.services.assets.bundle`: both answer "what does this asset
hand to something downstream", but a bundle answers it for an IMAGE GENERATOR
(prompt + N references trimmed to a provider ceiling, negatives recorded) while
this answers it for a CONVERSATION (one primary image the model can fetch on
demand, plus the text that keeps the subject consistent).

Why not just call ``build_bundle`` (recon §4.4):

* ``build_bundle`` requires ``caps: ProviderCapabilities``, i.e. an image model
  name. A chat turn has an LLM, not an image model — fabricating caps only to
  borrow a reference ceiling would push the generation protocol into the
  conversation protocol.
* Chat wants the SUBSET: the primary image, and ``positive`` as the consistency
  text. ``negative`` reaches no provider here at all and is deliberately absent
  (ruling D), as is the ``user_text`` tail — the user's own words are already
  the chat message.

What IS shared is every rule that could drift: :func:`reference_order` picks the
primary (so "the primary image" means the same thing as it does on the canvas),
:func:`dedupe_fragments` and :func:`linked_positive_texts` compose the text (so
the same asset does not describe itself two ways on two surfaces).

Model Experience
----------------

**What the model sees.** Nothing directly — this module produces a
:class:`ChatAssetRef`, and ``prompt_composer.render_available_resources``
renders it as one line inside ``<available_resources>``::

    <asset id="…" type="character" name="…" scope="…" primary_resource_id="…"
           has_image="true" loadout="…">consistency prompt text</asset>

The element body is ``consistency_prompt``; every attribute is one field of the
dataclass. The image itself is NOT inlined: ``primary_resource_id`` names an
ordinary ``resources`` row that the model fetches with ``ResourceFetch`` when it
decides it needs to look (ruling F), which is why ``has_image`` is a separate
field from ``primary_resource_id`` — an audio asset has a primary resource and
no image, and the model must be able to tell those apart before spending a tool
call.

**Token effect.** One asset costs roughly 40 tokens of attributes plus its
consistency prompt. That prompt is the only unbounded input (an asset's own
prompt, its loadout's extra note, and every linked costume/prop/location's
prompt, concatenated), so it is hard-capped at
:data:`MAX_CONSISTENCY_PROMPT_CHARS` characters with a visible ``[truncated]``
marker rather than left to grow with the number of links. Total growth is
therefore bounded by (number of asset attachments in the turn) × that cap; the
number of attachments is bounded by the composer's staged-attachment list.

**KV Cache effect.** These entries ride in ``<available_resources>``, which is
appended AFTER the system message's cache boundary (see
``app/services/ai/prompts/README.md``). Changing an asset's prompt, its loadout
or its links therefore changes only that suffix — it does not invalidate the
agent/skill prefix. Nothing in this module is cached itself: it is recomposed
from live rows on every turn, so an edit in the asset sheet is visible to the
next message with no invalidation step.

Known Limitations and Deferred Work
-----------------------------------

* ``prompt_negative`` is never delivered to chat (ruling D). An asset whose
  identity lives in its negatives reads as under-specified to the model.
* v1 clients never pick a loadout, so ``loadout_id`` is in practice the asset's
  default. The field exists because the resolver already knows the answer and a
  v2 picker should not need a wire change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.services.assets.bundle import linked_positive_texts
from app.services.assets.slot_generation import dedupe_fragments, reference_order
from app.services.assets.slots import PRIMARY_SLOT

# The consistency prompt is the one model-visible field with no natural ceiling
# (asset prompt + loadout note + every linked asset's prompt). 600 characters is
# ~150 tokens: enough for a full character description with two costumes, small
# enough that a dozen attachments cannot displace the conversation. Truncation
# is MARKED — a silently shortened prompt reads to the model as the whole
# description, which is exactly the failure "the asset looks wrong and nothing
# says why".
MAX_CONSISTENCY_PROMPT_CHARS = 600
TRUNCATION_MARKER = " [truncated]"

# Types whose primary slot holds an IMAGE. ``prompt`` has no file slot at all
# and ``audio``'s primary slot holds audio bytes, so neither can produce a
# picture — ruling E requires both to say so explicitly rather than be folded
# into "this asset has no image", which reads identically to a broken lookup.
_IMAGE_PRIMARY_TYPES = frozenset({"character", "location", "prop", "costume"})


@dataclass(frozen=True)
class ChatAssetRef:
    """One asset as the prompt composer will render it.

    ``asset_id`` / ``scope_id`` / ``primary_resource_id`` / ``loadout_id`` are
    STRINGS (or None) because they are Snowflake BIGINTs: they cross the wire to
    a browser, and a JS number loses precision above 2^53. ``has_image`` is a
    real bool, not the rendered ``"true"``/``"false"`` — the renderer owns that
    spelling.
    """

    asset_id: str
    name: str
    asset_type: str
    scope_id: Optional[str]
    primary_resource_id: Optional[str]
    has_image: bool
    consistency_prompt: str
    loadout_id: Optional[str]


def expects_primary_image(asset_type: str) -> bool:
    """Whether a missing/unusable primary image is worth reporting for this type.

    ``prompt`` and ``audio`` assets are not broken for having no picture, so a
    caller must not raise ``asset_no_primary_image`` on them (ruling C scopes
    that reason to "types that are expected to have one").
    """
    return asset_type in _IMAGE_PRIMARY_TYPES


def _truncate(text: str) -> str:
    if len(text) <= MAX_CONSISTENCY_PROMPT_CHARS:
        return text
    return text[:MAX_CONSISTENCY_PROMPT_CHARS] + TRUNCATION_MARKER


def _pick_primary(
    files_by_slot: Dict[str, List[Dict[str, Any]]], asset_type: str
) -> tuple[Optional[str], bool]:
    """``(primary_resource_id, has_image)`` for one asset's file map.

    ``reference_order(..., max_refs=1)`` rather than "the first row in the
    primary slot": that function already encodes the priority (primary slot
    first, then ``worn``/``stills``/the rest) and its own comment guarantees the
    primary is never the entry trimmed away. Reimplementing the pick here is how
    the canvas and the chat would start disagreeing about which image IS the
    asset.

    ``has_image`` is read off the picked row, where the caller stamped it from
    the ``resources`` rows (the same ``_reference_stored_path`` ladder
    ``AssetsService._stamp_image_availability`` uses). Following ``bundle``'s
    convention, only an explicit ``False`` disqualifies: an ABSENT key means "the
    caller did not look it up", and treating that as "no image" would make every
    asset silently image-less for any future caller that skips the stamping step.
    """
    ordered = reference_order(files_by_slot, asset_type, max_refs=1)
    if not ordered:
        return None, False
    primary = ordered[0]
    for rows in files_by_slot.values():
        for row in rows or []:
            if int(row["resource_id"]) == primary:
                return str(primary), row.get("has_image") is not False
    # reference_order only ever returns ids it read out of this same map, so
    # this is unreachable; answering "no image" beats raising on a chat turn.
    return str(primary), False


def build_chat_ref(
    asset_row: Dict[str, Any],
    loadout_row: Optional[Dict[str, Any]],
    linked_assets: Sequence[Dict[str, Any]],
    files_by_slot: Dict[str, List[Dict[str, Any]]],
) -> ChatAssetRef:
    """Expand one asset into the reference a chat turn delivers.

    Composition order for ``consistency_prompt`` follows ruling D, which is
    ``build_bundle``'s order minus the two pieces chat has no use for::

        asset.prompt_positive → loadout.prompt_extra → linked costumes → linked
        props → linked location

    ``user_text`` is absent (the chat message IS the user's text) and so is
    ``negative`` (nothing downstream of a chat turn consumes one).

    Two types take an explicit branch rather than falling through, because
    folding them into the general case produces an entry that is well-formed and
    wrong (ruling E):

    ``prompt``
        Has no file slot (``PRIMARY_SLOT['prompt'] is None``); its BODY is the
        content. ``primary_resource_id`` is None and ``has_image`` is False, and
        the consistency prompt is the body alone — a prompt asset's description
        is metadata about the prompt, not part of it.
    ``audio``
        Its primary slot holds AUDIO. ``primary_resource_id`` is still given (the
        model can fetch and transcribe it) but ``has_image`` is False, and the
        description joins the text because for a voice or an ambience the
        description is what carries the direction.
    """
    asset_type = str(asset_row.get("asset_type") or "")
    if asset_type not in PRIMARY_SLOT:
        # Same posture as ``slots.readiness`` / ``slot_generation._slot_priority``:
        # an unknown type fails loudly instead of composing a plausible entry.
        raise ValueError(f"unknown asset_type: {asset_type!r}")

    own_prompt = str(asset_row.get("prompt_positive") or "")
    description = str(asset_row.get("description") or "")

    if asset_type == "prompt":
        fragments = [own_prompt]
        primary_resource_id, has_image = None, False
    else:
        if asset_type == "audio":
            leading = [description, own_prompt]
        else:
            leading = [own_prompt]
        fragments = [
            *leading,
            str((loadout_row or {}).get("prompt_extra") or ""),
            *linked_positive_texts(linked_assets),
        ]
        primary_resource_id, has_image = _pick_primary(files_by_slot, asset_type)
        if asset_type == "audio":
            # A primary resource, and deliberately not an image: the model must
            # not spend a ResourceFetch(mode=image) on a .wav.
            has_image = False

    scope_id = asset_row.get("scope_id")
    loadout_id = (loadout_row or {}).get("id")
    return ChatAssetRef(
        asset_id=str(asset_row["id"]),
        name=str(asset_row.get("name") or ""),
        asset_type=asset_type,
        scope_id=None if scope_id is None else str(scope_id),
        primary_resource_id=primary_resource_id,
        has_image=has_image,
        consistency_prompt=_truncate(", ".join(dedupe_fragments(fragments))),
        loadout_id=None if loadout_id is None else str(loadout_id),
    )


__all__ = [
    "MAX_CONSISTENCY_PROMPT_CHARS",
    "TRUNCATION_MARKER",
    "ChatAssetRef",
    "build_chat_ref",
    "expects_primary_image",
]
