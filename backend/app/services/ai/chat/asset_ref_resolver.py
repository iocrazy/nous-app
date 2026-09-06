"""Resolve attachments whose ``kind == 'asset_ref'`` into :class:`ChatAssetRef`
entries the prompt composer renders inside ``<available_resources>``.

The asset-library sibling of :mod:`resource_ref_resolver`, and deliberately the
same shape: re-validate access SERVER-SIDE from ``user_id`` alone, load no
content (the primary image reaches the model only if it calls ``ResourceFetch``
on ``primary_resource_id`` — ruling F), and report every dropped reference with
a reason instead of thinning the list in silence.

**Why there is no ``scope_id`` parameter** (recon §5, ruling B). Every
``/assets`` endpoint takes a required ``scope_id`` and gates it with
``assets_router._gate``. A chat turn has no scope to give: the floating chat
window outlives any one route, and ``AILibraryChatService.chat()`` takes no
workspace argument. So access is answered the way the RESOURCE path already
answers it — by team membership — via
``AssetsRepository.list_accessible(user_id=…)``. That method's predicate is
``get``'s predicate with the single scope replaced by the caller's whole team
set, and the equivalence is pinned behaviourally in
``tests/services/ai/chat/test_asset_ref_resolver.py`` rather than asserted in
prose: two authorization opinions about the same asset is precisely the drift
this feature could not survive.

**The ``Resources`` read is scoped.** ``has_image`` needs the ``resources``
rows behind the asset's files, and ``Resources`` carries
``UserScoped(creator_id)`` while this path binds no tenant scope.
``SCOPE_ENFORCE_RESOURCES`` defaults to false in code but production sets it
TRUE via ``secrets/backend.env`` (CLAUDE.md 部署陷阱), so the
``is_enforced``-gated ``system_request_scope`` wrap is LOAD-BEARING there and a
no-op here — unit tests cannot fail for its absence, which is why
``test_asset_ref_resolver.py`` asserts the scope is entered rather than trusting
the code to look right. The wrap itself lives in
``AssetRelationsRepository.resource_media_rows``, which already exists for the
same reason on the canvas path; going around it with a bare ``select(Resources)``
here would be the second copy that eventually loses the wrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from loguru import logger

from app.repositories.asset_relations_repository import AssetRelationsRepository
from app.repositories.assets_repository import AssetsRepository
from app.services.assets.chat_ref import (
    ChatAssetRef,
    build_chat_ref,
    expects_primary_image,
)
from app.services.assets.slot_generation import files_by_slot
from app.services.assets.slots import PRIMARY_SLOT

_MEDIA_READ_REASON = "chat-asset-ref-primary-image-availability"

# The closed vocabulary every `attachment_failures` entry on the REFERENCE
# path draws from. Declared here because this is where five of the six are
# produced; the sixth (`attachment_limit_exceeded`) is emitted by
# `ai_library_chat_service` — the cap counts `asset_ref` entries only (resource
# refs are uncapped by ruling), and it is applied before this resolver runs. One list, so a frontend adding copy for
# a new code has one place to read.
#
# ⚠️ This list is MIRRORED by `frontend/components/chat/AttachmentFailureBanner
# .tsx`'s `NAMED_REASONS` and by `chat.attachmentFailureReason.*` in BOTH
# locales. `tests/services/ai/chat/test_attachment_limit_frontend_mirror.py`
# reads those files and fails on either half of the drift: a code the banner
# does not name lands in its counted bucket (degraded, silent), and a code with
# no locale string renders the raw key at a user.
AssetRefFailureReason = Literal[
    "asset_not_accessible",
    "asset_deleted",
    "asset_no_primary_image",
    "asset_type_unknown",
    "loadout_not_owned",
    "attachment_limit_exceeded",
]


@dataclass(frozen=True)
class AssetRefFailure:
    """One asset reference that could not be delivered whole.

    ``index`` is the position in the ``attachments`` list handed to
    :func:`resolve_asset_refs` — NOT a position among the asset refs. Pass the
    turn's full attachment list and the index means what the UI needs it to
    mean; pass a pre-filtered bucket and it points at the wrong chip.

    ``asset_no_primary_image`` is the one reason that does NOT drop the
    reference: the asset is still delivered (its consistency prompt is the point
    of mentioning it), and the failure says the model cannot see a picture of
    it. Reporting nothing there would leave the user wondering why the answer
    ignores what the character looks like; dropping the whole entry would throw
    away the text that did resolve.
    """

    index: int
    reason: AssetRefFailureReason


def coerce_asset_id(value: Any) -> Optional[str]:
    """The attachment's ``asset_id`` as a decimal string, or None if unusable.

    The wire carries these as STRINGS (``assets`` router ``str()``s every
    Snowflake so a JS number cannot lose the low bits) — but a hand-built
    request, a test fixture, or any future client that forgets can send a JSON
    number, and ``"123" != 123`` would silently make that asset unresolvable.
    Normalizing here rather than at each comparison is the fix for the class of
    bug the storyboard canvas paid for once (CLAUDE.md 边界 mock).

    Public because the chat service has to map a :class:`ChatAssetRef` back to
    the attachment that asked for it (to report a failure against the right
    chip), and doing that with a second, slightly different normalization is
    how the two would disagree about which attachment an asset came from.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(int(text))
    except ValueError:
        return None


async def _pick_loadout(
    relations: AssetRelationsRepository, asset_id: int, requested: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[AssetRefFailureReason]]:
    """``(loadout, failure_reason)`` for one reference.

    Three outcomes, and they are three different facts:

    * a ``requested`` id the asset OWNS → ``(that loadout, None)``;
    * a ``requested`` id it does not own → ``(None, "loadout_not_owned")``, and
      the caller DROPS the reference;
    * nothing requested → ``(the default loadout or None, None)`` (ruling D).

    **Why the foreign id is refused rather than defaulted.** v1 fell back to the
    default and logged: ruling C fixed the user-visible vocabulary at four codes,
    none of which meant "that outfit is not this character's", and no v1 client
    could send the input anyway — both entry points hardcoded ``null``. The v2
    loadout picker on the staged chip makes it reachable by a real person, and a
    fallback then becomes a silent substitution of something they explicitly
    chose. So the deferred fifth reason this docstring used to promise now
    exists, and the reference goes away with it: a picture of the WRONG outfit,
    delivered without comment, is worse than no picture and a sentence saying
    why. ``AssetsService._owned_loadout`` raises a typed 422 on the same input;
    this is the same refusal in the shape ``attachment_failures`` speaks.

    An asset with NO loadouts is refused too when one was requested. Returning
    "no loadout" there is right for an unrequested loadout and wrong for a
    requested one — the user asked for something this asset cannot provide, and
    silence would tell the same lie the foreign-id case tells.
    """
    loadouts = await relations.list_loadouts(int(asset_id))
    if requested is not None:
        owned = {str(lo["id"]): lo for lo in loadouts}.get(requested)
        if owned is not None:
            return owned, None
        logger.warning(
            f"[asset_ref_resolver] loadout {requested!r} does not belong to asset "
            f"{asset_id} — the reference is refused, not re-dressed"
        )
        return None, "loadout_not_owned"
    for lo in loadouts:
        if lo.get("is_default"):
            return lo, None
    return None, None


async def _linked_rows(
    assets: AssetsRepository,
    relations: AssetRelationsRepository,
    user_id: str,
    asset_id: int,
    loadout: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """The costume/prop rows this reference is dressed in, in delivery order.

    Mirrors ``AssetsService._linked_asset_rows``: with a loadout, exactly its
    ``costume_ids`` then ``prop_ids`` in the stored order (the loadout is a
    FILTER — a costume linked to the character but left out of the outfit must
    not leak in); without one, every ``wears`` then every ``holds`` target,
    each sorted so two identical turns compose the same text.

    The rows come back through ``list_accessible`` rather than
    ``get(id, scope_id)`` for the same reason the subject does: there is no
    scope here. A target the user cannot read is skipped, which is the same
    outcome the scoped version produces.
    """
    if loadout is not None:
        target_ids = [int(c) for c in (loadout.get("costume_ids") or [])] + [
            int(p) for p in (loadout.get("prop_ids") or [])
        ]
    else:
        target_ids = sorted(await relations.link_targets(asset_id, "wears")) + sorted(
            await relations.link_targets(asset_id, "holds")
        )
    if not target_ids:
        return []
    rows = await assets.list_accessible(user_id, asset_ids=target_ids)
    by_id = {int(r["id"]): r for r in rows}
    return [by_id[t] for t in target_ids if t in by_id]


async def _stamp_image_availability(
    relations: AssetRelationsRepository,
    slot_maps: Sequence[Dict[str, List[Dict[str, Any]]]],
) -> None:
    """Mark every file row across EVERY referenced asset with ``has_image``.

    One batched ``resources`` read for the whole turn rather than one per asset:
    the ladder (original image → thumbnail → cover) and the system-scope wrap
    both live in ``resource_media_rows`` / ``_reference_stored_path``, so "this
    file can be shown" means the same thing here, on the canvas, and in a
    generate-slot run.
    """
    from app.services.assets.assets_service import _reference_stored_path

    candidates = {
        int(f["resource_id"])
        for slot_map in slot_maps
        for rows in slot_map.values()
        for f in rows
    }
    if not candidates:
        return
    media = await relations.resource_media_rows(
        sorted(candidates), system_reason=_MEDIA_READ_REASON
    )
    available = {
        rid for rid in candidates if _reference_stored_path(media.get(rid) or {})
    }
    for slot_map in slot_maps:
        for rows in slot_map.values():
            for f in rows:
                f["has_image"] = int(f["resource_id"]) in available


async def resolve_asset_refs(
    attachments: List[dict] | None, *, user_id: str
) -> Tuple[List[ChatAssetRef], List[AssetRefFailure]]:
    """Return ``(refs_for_prompt, typed_failures)`` for the turn's asset refs.

    ``refs_for_prompt`` is a list of :class:`ChatAssetRef` DATACLASSES, not
    dicts (the P5 plan's Task 1 line said ``list[dict]``; the ruling is that the
    dataclass wins, because the field names are a contract three tasks share and
    a dict lets a typo become a missing attribute at render time). Callers read
    attributes — ``ref.primary_resource_id`` — and reach for
    ``dataclasses.asdict`` only where a mapping is genuinely required.

    Only dicts whose ``kind`` is exactly ``"asset_ref"`` are considered;
    everything else is ignored (and still counted, so failure indices stay
    aligned with the caller's list). A repeated ``asset_id`` resolves once —
    the second mention would render a duplicate line without adding anything —
    and any failure is reported against the FIRST mention's index.

    Failure vocabulary (ruling C, amended by final review I2 with the limit
    code and by v2 with ``loadout_not_owned``), all of which reach the user
    through ``attachment_failures``:

    ``asset_not_accessible``
        No row the caller can read, and no ``asset_id`` we could parse. The
        catch-all: the asset belongs to a team they left, or the id is junk.
    ``asset_deleted``
        The row exists and IS theirs, but is soft-deleted. Told apart from the
        above by a second query with the same visibility predicate and the
        ``deleted_at`` filter dropped — the two answers send the user to
        different places (the trash, versus asking for access).
    ``asset_no_primary_image``
        A type expected to have a picture (character / location / prop /
        costume) has none the model could fetch. The reference is still
        delivered; see :class:`AssetRefFailure`.
    ``asset_type_unknown``
        The row's ``asset_type`` is outside the six the slot table knows, so
        neither the primary image nor readiness can be computed. Dropped, and
        loudly — the same posture ``slots.readiness`` takes, because a renamed
        or typo'd type must not come back as a well-formed entry with no image.
    ``loadout_not_owned``
        A ``loadout_id`` was requested and the asset does not own it. Dropped:
        the v2 picker means somebody CHOSE that outfit, so quietly composing a
        different one would substitute for a decision rather than report a
        problem. See :func:`_pick_loadout`.
    ``attachment_limit_exceeded``
        NOT produced here. The turn carried more ``asset_ref`` attachments
        than ``ai_library_chat_service.MAX_ASSET_REF_ATTACHMENTS``, so this one
        was never resolved. Listed in this vocabulary because it arrives in the
        same ``attachment_failures`` list and needs the same UI copy; raised one
        level up because this function is handed an ALREADY-CAPPED list and
        cannot see what was refused.
    """
    if not attachments:
        return [], []

    first_index: Dict[str, int] = {}
    order: List[str] = []
    requested_loadout: Dict[str, Optional[str]] = {}
    bad_indices: List[int] = []
    for idx, att in enumerate(attachments):
        if not isinstance(att, dict) or att.get("kind") != "asset_ref":
            continue
        asset_id = coerce_asset_id(att.get("asset_id"))
        if asset_id is None:
            bad_indices.append(idx)
            logger.info(
                f"[asset_ref_resolver] unusable asset_id "
                f"{att.get('asset_id')!r} at index {idx} user={user_id}"
            )
            continue
        if asset_id in first_index:
            continue
        first_index[asset_id] = idx
        order.append(asset_id)
        # Same normalization as the asset id, and for the same reason: the
        # loadout Snowflake rides the wire as a string but need not.
        requested_loadout[asset_id] = coerce_asset_id(att.get("loadout_id"))

    failures: List[AssetRefFailure] = [
        AssetRefFailure(index=i, reason="asset_not_accessible") for i in bad_indices
    ]
    if not order:
        return [], failures

    assets = AssetsRepository()
    relations = AssetRelationsRepository()

    rows = await assets.list_accessible(user_id, asset_ids=order)
    by_id = {str(r["id"]): r for r in rows}

    missing = [aid for aid in order if aid not in by_id]
    deleted_ids: set[str] = set()
    if missing:
        # Same visibility predicate, ``deleted_at`` filter dropped. A second
        # membership query written here instead would be the drift ruling B
        # exists to prevent.
        deleted_rows = await assets.list_accessible(
            user_id, asset_ids=missing, include_deleted=True
        )
        deleted_ids = {str(r["id"]) for r in deleted_rows}

    # Everything each surviving asset needs, gathered before the single batched
    # ``resources`` read that stamps ``has_image`` across all of them.
    #
    # ⚠️ Cost: FOUR round trips per asset (loadouts, files, and the two
    # ``link_targets`` traversals), against one batched ``resources`` read for
    # the whole turn. Left per-asset deliberately — batching them needs three
    # new multi-asset repository methods, and the loop is now BOUNDED:
    # ``ai_library_chat_service.MAX_ASSET_REF_ATTACHMENTS`` (8) caps how many
    # asset refs one turn resolves, so the worst case is ~40 serial round trips
    # rather than the unbounded one final review I2 found. Revisit if that cap
    # rises or a caller starts resolving asset refs in bulk.
    staged: List[Tuple[str, Dict[str, Any], Optional[Dict], List[Dict], Dict]] = []
    for asset_id in order:
        row = by_id.get(asset_id)
        if row is None:
            failures.append(
                AssetRefFailure(
                    index=first_index[asset_id],
                    reason=(
                        "asset_deleted"
                        if asset_id in deleted_ids
                        else "asset_not_accessible"
                    ),
                )
            )
            logger.info(
                f"[asset_ref_resolver] dropped ref id={asset_id!r} "
                f"deleted={asset_id in deleted_ids} user={user_id}"
            )
            continue
        if str(row.get("asset_type") or "") not in PRIMARY_SLOT:
            failures.append(
                AssetRefFailure(
                    index=first_index[asset_id], reason="asset_type_unknown"
                )
            )
            logger.warning(
                f"[asset_ref_resolver] asset {asset_id} has unknown asset_type "
                f"{row.get('asset_type')!r} — dropped"
            )
            continue
        native_id = int(row["id"])
        loadout, loadout_failure = await _pick_loadout(
            relations, native_id, requested_loadout.get(asset_id)
        )
        if loadout_failure is not None:
            failures.append(
                AssetRefFailure(index=first_index[asset_id], reason=loadout_failure)
            )
            continue
        linked = await _linked_rows(assets, relations, user_id, native_id, loadout)
        slot_map = files_by_slot(await relations.list_files(native_id), loadout)
        staged.append((asset_id, row, loadout, linked, slot_map))

    await _stamp_image_availability(relations, [s[4] for s in staged])

    refs: List[ChatAssetRef] = []
    for asset_id, row, loadout, linked, slot_map in staged:
        ref = build_chat_ref(row, loadout, linked, slot_map)
        refs.append(ref)
        if expects_primary_image(ref.asset_type) and not ref.has_image:
            failures.append(
                AssetRefFailure(
                    index=first_index[asset_id], reason="asset_no_primary_image"
                )
            )
    failures.sort(key=lambda f: f.index)
    return refs, failures


__all__ = [
    "AssetRefFailure",
    "AssetRefFailureReason",
    "coerce_asset_id",
    "resolve_asset_refs",
]
