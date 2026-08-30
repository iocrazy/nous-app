"""AssetsService — the invariants layer (spec §3, §7).

Repos are dumb; this is where slot validity, link-type rules, loadout ⊆ links,
default-loadout-on-character, 409-on-duplicate and system-preset read-only are
enforced. Every failure is a typed AssetError the router maps 1:1 to HTTP —
no silent no-ops (CLAUDE.md "触发路径必须类型化失败回显").
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.db.engine import is_configured
from app.db.session import maybe_unit_of_work
from app.repositories.asset_relations_repository import (
    AssetRelationsRepository,
    _serialize_file,
    _serialize_link,
    _serialize_loadout,
)
from app.repositories.assets_repository import (
    AssetsRepository,
    DuplicateAssetName,
    _serialize,
    with_derived,
)
from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    DuplicateRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
    PromptTranslateRequest,
)
from app.services.assets.slots import PRIMARY_SLOT, is_valid_slot, link_allowed
from app.services.library.resource_ai_ops import (
    CaptionAgentFailed,
    CaptionAgentPaused,
    CaptionSourceUnavailable,
    build_translate_plan,
    caption_resource_for_caller,
    is_provider_failure,
    translate_fields,
)
from app.services.library.resources_service import _resolve_personal_team_id


class AssetError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.status, self.code, self.detail = status, code, detail
        self.extra = extra or {}
        super().__init__(f"{code}: {detail}")


# The nullable columns — the only ones an explicit null may clear. Everything
# else on ``assets`` is NOT NULL, so a null aimed at it is a typed 422 rather
# than an IntegrityError (500) or a silently dropped key (a no-op reported as
# success). Mirrors the AssetUpdate docstring.
CLEARABLE_FIELDS = frozenset(
    {
        "subtype",
        "cover_file_id",
        "prompt_positive",
        "prompt_negative",
        "prompt_positive_zh",
        "prompt_negative_zh",
    }
)

# The header a duplicate carries over verbatim. Everything NOT here is decided
# by ``duplicate`` (name / source / duplicated_from / is_system_preset /
# scope_id / cover_file_id) or left to the column default (id, the timestamps,
# ``sort_order`` — a manual shelf position belongs to the row that earned it).
# An explicit tuple rather than "every column except…": a new column added to
# ``assets`` should have to be classified once, here, instead of joining the
# copy silently.
DUPLICATED_FIELDS = (
    "asset_type",
    "subtype",
    "role_tag",
    "description",
    "attrs",
    "prompt_positive",
    "prompt_negative",
    "prompt_positive_zh",
    "prompt_negative_zh",
    "platform_params",
    "tags",
)


# The asset shelf's own (en_field, zh_field) pairs, handed to the SHARED
# ``build_translate_plan`` (which defaults to the ``resources`` column names).
# One planner for both surfaces: the "skip an empty source" rule is the same
# rule, and a second copy is how the two quietly stop agreeing.
ASSET_PROMPT_FIELD_PAIRS = [
    ("prompt_positive", "prompt_positive_zh"),
    ("prompt_negative", "prompt_negative_zh"),
]

# Said when the agent ran but produced nothing. Deliberately the same wording
# the resources endpoint uses for its 502 — it is the same misconfiguration,
# and the user fixes it in the same place.
_TRANSLATE_EMPTY = (
    "Translation produced no result — check the translation agent's provider "
    "configuration in Settings → AI"
)


class AssetsService:
    def __init__(
        self,
        assets_repo: Optional[AssetsRepository] = None,
        relations_repo: Optional[AssetRelationsRepository] = None,
    ):
        self.assets = assets_repo or AssetsRepository()
        self.relations = relations_repo or AssetRelationsRepository()

    # ── helpers ────────────────────────────────────────────────────────────

    async def _require(self, asset_id: int, scope_id: int) -> Dict[str, Any]:
        row = await self.assets.get(int(asset_id), int(scope_id))
        if not row:
            raise AssetError(404, "asset_not_found", "Asset not found")
        return row

    async def _require_writable(self, asset_id: int, scope_id: int) -> Dict[str, Any]:
        """``_require`` + the system-preset read-only gate (spec §7.0).

        ``AssetsRepository.get`` unions ``is_system_preset`` into EVERY scope, so
        a preset row is reachable from any team. The header-column writes carry a
        scope predicate a preset (scope_id NULL) never matches, but the relation
        tables (asset_files / asset_links / asset_loadouts / asset_project_refs)
        key on ``asset_id`` alone — nothing there would stop team T from writing
        to the global row and team U from reading the result. Every mutating path
        goes through this, not ``_require``.
        """
        row = await self._require(asset_id, scope_id)
        if row.get("is_system_preset"):
            raise AssetError(
                403,
                "system_preset_readonly",
                "System presets are read-only; duplicate to edit",
            )
        return row

    async def _derived(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ids = [int(r["id"]) for r in rows]
        sc, pi, lc = (
            await self.assets.slot_counts(ids),
            await self.assets.project_ids(ids),
            await self.assets.loadout_counts(ids),
        )
        return [_serialize(with_derived(r, sc, pi, lc)) for r in rows]

    # ── assets ─────────────────────────────────────────────────────────────

    async def create_asset(
        self, scope_id: int, payload: AssetCreate, user_id: Optional[str]
    ) -> Dict[str, Any]:
        fields = payload.model_dump()
        try:
            row = await self.assets.create(int(scope_id), fields, user_id)
        except DuplicateAssetName as e:
            raise AssetError(
                409,
                "asset_exists",
                "An asset with this name and type already exists in this scope",
                {"existing_asset_id": str(e.existing_id)},
            )
        if row["asset_type"] == "character":
            await self.relations.create_loadout(
                int(row["id"]), {"name": "Default", "is_default": True}
            )
        return (await self._derived([row]))[0]

    async def list_assets(
        self,
        scope_id: int,
        *,
        readiness: Optional[str] = None,
        sort: str = "recent",
        **filters,
    ) -> List[Dict[str, Any]]:
        """List a scope's assets.

        ``readiness`` and ``sort="readiness"`` are handled HERE, not in SQL:
        readiness is derived per row (``with_derived`` folds the batch slot
        counts in), so there is no column to filter or order by. Everything
        else — ``asset_type`` / ``project_id`` / ``q`` / ``tag`` / the two SQL
        orderings / limit / offset — is delegated unchanged.

        Known limitation: because the readiness filter runs after the page has
        been fetched, it thins THAT page rather than paging over the filtered
        set. A caller asking for 60 drafts can get fewer back with more still
        behind the offset. Making it exact needs the primary-slot count in the
        query (a join this repo does not have yet).
        """
        rows = await self.assets.list(
            int(scope_id),
            sort=("recent" if sort == "readiness" else sort),
            **filters,
        )
        out = await self._derived(rows)
        if readiness:
            out = [r for r in out if r["readiness"]["state"] == readiness]
        if sort == "readiness":
            # Drafts first: the point of this ordering is surfacing what is
            # still missing. Python's sort is stable, so rows keep the repo's
            # "recent" order inside each group.
            out.sort(key=lambda r: 0 if r["readiness"]["state"] == "draft" else 1)
        return out

    async def get_asset(self, asset_id: int, scope_id: int) -> Dict[str, Any]:
        row = await self._require(asset_id, scope_id)
        out = (await self._derived([row]))[0]
        files = await self.relations.list_files(int(asset_id))
        outgoing, incoming = await self.relations.list_links(int(asset_id))
        loadouts = await self.relations.list_loadouts(int(asset_id))
        out["files"] = [_serialize_file(f) for f in files]
        out["links"] = [_serialize_link(link) for link in outgoing]
        out["linked_by"] = [_serialize_link(link) for link in incoming]
        out["loadouts"] = [_serialize_loadout(lo) for lo in loadouts]
        return out

    async def update_asset(
        self, asset_id: int, scope_id: int, payload: AssetUpdate
    ) -> Dict[str, Any]:
        await self._require_writable(asset_id, scope_id)
        # exclude_unset == model_fields_set: omitted keys never reach the
        # UPDATE, an explicit null does (see AssetUpdate's docstring). With
        # exclude_none the two were indistinguishable and "clear this field"
        # answered 200 while changing nothing.
        fields = payload.model_dump(exclude_unset=True)
        nulled = sorted(
            k for k, v in fields.items() if v is None and k not in CLEARABLE_FIELDS
        )
        if nulled:
            raise AssetError(
                422,
                "field_not_nullable",
                "These fields cannot be cleared: " + ", ".join(nulled),
                {"fields": nulled},
            )
        if fields.get("cover_file_id") is not None:
            # A cover is a resource reference; without this check an asset could
            # point at another team's file — a 200 for a cross-tenant read.
            # A null skips it: there is no resource to be in scope.
            if not await self.relations.resource_in_scope(
                int(fields["cover_file_id"]), int(scope_id)
            ):
                raise AssetError(
                    404, "resource_not_found", "Cover file not found in this scope"
                )
            fields["cover_file_id"] = int(fields["cover_file_id"])
        try:
            updated = await self.assets.update(int(asset_id), int(scope_id), fields)
        except DuplicateAssetName as e:
            raise AssetError(
                409,
                "asset_exists",
                "Name already used by another asset of this type",
                {"existing_asset_id": str(e.existing_id)},
            )
        if not updated:
            # The row was soft-deleted (or left the scope) between _require and
            # the UPDATE. update() returns Optional; feeding None to _derived
            # would be a TypeError → 500 instead of the honest 404.
            raise AssetError(404, "asset_not_found", "Asset not found")
        return (await self._derived([updated]))[0]

    async def duplicate(
        self,
        asset_id: int,
        scope_id: int,
        user_id: Optional[str],
        req: DuplicateRequest,
    ) -> Dict[str, Any]:
        """Copy an asset into the caller's scope, whole (spec §5.1 / P2).

        ``_require``, NOT ``_require_writable``: duplicating a system preset is
        exactly how a preset gets edited — every direct write to one is a 403 —
        so any member may copy one. The COPY is a normal row of the caller's
        scope (``is_system_preset=False``, ``scope_id`` = theirs).

        What comes along, and what deliberately does not:

        * header — ``DUPLICATED_FIELDS`` verbatim (deep-copied: ``attrs`` /
          ``tags`` / ``platform_params`` are mutable, and handing the new row
          the SAME object would make an edit to the copy rewrite the original).
        * ``cover_file_id`` — only if the resource is in the caller's scope.
          A preset's cover is not, and carrying the id over unchecked would be
          the cross-tenant reference ``update_asset`` already refuses.
        * loadouts — name / prompt_extra / sort_order / costume_ids / prop_ids,
          ``is_default`` preserved, **the default created first**
          (``uq_loadout_default`` is a partial unique index PostgreSQL checks
          row by row and cannot defer). This is also why the row is inserted
          through the repo instead of ``create_asset``: that one adds a
          "Default" loadout of its own, which would be a second default.
        * files — same resource / slot / sort_order / note, with
          ``loadout_id`` REMAPPED onto the new loadout rows. Copying it
          verbatim would leave the copy's files pointing at the SOURCE's
          loadouts: a copy that looks complete and behaves as someone else's.
          A file whose resource is not in the caller's scope is skipped (a
          preset has none, and the copy must not reference what the caller
          cannot see).
        * links — OUTGOING only. An incoming link is someone else's statement
          about the source; copying it would make that audio voice two
          characters.
        * project refs — not copied. A copy is not yet used anywhere.

        All of it in ONE transaction: a half-copied asset (header committed,
        files not) is worse than no copy, because it is a row the user has to
        find and delete by hand.
        """
        src = await self._require(asset_id, scope_id)
        name = req.name or f"{src['name']} (copy)"

        fields: Dict[str, Any] = {
            key: copy.deepcopy(src[key]) for key in DUPLICATED_FIELDS
        }
        fields.update(
            {
                "scope_id": int(scope_id),
                "name": name,
                "source": "duplicated",
                "duplicated_from": int(src["id"]),
                "is_system_preset": False,
                "created_by": user_id,
                "cover_file_id": await self._cover_for_copy(src, scope_id),
            }
        )

        async with maybe_unit_of_work(is_configured()):
            clash = await self.assets.find_by_name(
                int(scope_id), src["asset_type"], name
            )
            if clash:
                raise AssetError(
                    409,
                    "asset_exists",
                    "An asset with this name and type already exists in this scope",
                    {"existing_asset_id": str(clash["id"])},
                )
            try:
                row = await self.assets.create_raw(fields)
            except DuplicateAssetName as e:
                # Lost the race with a concurrent insert between the check and
                # this one. ``create_raw`` cannot look the winner's id up (its
                # transaction is aborted), so the 409 goes out without the
                # extra rather than not at all.
                raise AssetError(
                    409,
                    "asset_exists",
                    "An asset with this name and type already exists in this scope",
                    (
                        {"existing_asset_id": str(e.existing_id)}
                        if e.existing_id
                        else {}
                    ),
                )
            new_id = int(row["id"])
            loadout_map = await self._copy_loadouts(
                int(asset_id), new_id, row["asset_type"]
            )
            await self._copy_files(
                int(asset_id), new_id, scope_id, loadout_map, user_id
            )
            outgoing, _incoming = await self.relations.list_links(int(asset_id))
            for link in outgoing:
                await self.relations.add_link(
                    new_id, int(link["to_asset_id"]), link["relation"]
                )
        # No touch_asset: the row was INSERTed in this same transaction, so its
        # updated_at is already now() — the bump exists for relation writes
        # against a row whose header did not change.
        return await self.get_asset(new_id, int(scope_id))

    async def _cover_for_copy(
        self, src: Dict[str, Any], scope_id: int
    ) -> Optional[int]:
        cover = src.get("cover_file_id")
        if cover is None:
            return None
        if not await self.relations.resource_in_scope(int(cover), int(scope_id)):
            return None
        return int(cover)

    async def _copy_loadouts(
        self, src_id: int, new_id: int, asset_type: str
    ) -> Dict[int, int]:
        """Copy every loadout, default first; returns old id → new id.

        The ordering is not cosmetic — see ``duplicate``'s docstring. The
        source list is re-sorted here rather than trusted: ``list_loadouts``
        does order default-first today, but this invariant must not depend on
        a repo ORDER BY staying that way.
        """
        rows = sorted(
            await self.relations.list_loadouts(src_id),
            key=lambda lo: (not lo["is_default"], lo.get("sort_order", 0)),
        )
        if not rows and asset_type == "character":
            # ``create_asset`` guarantees every character has a default loadout.
            # A source seeded without one (a preset) must not produce a copy
            # that breaks the invariant for every path that assumes it.
            await self.relations.create_loadout(
                new_id, {"name": "Default", "is_default": True}
            )
            return {}
        mapping: Dict[int, int] = {}
        for lo in rows:
            created = await self.relations.create_loadout(
                new_id,
                {
                    "name": lo["name"],
                    "is_default": bool(lo["is_default"]),
                    "costume_ids": [int(c) for c in (lo.get("costume_ids") or [])],
                    "prop_ids": [int(p) for p in (lo.get("prop_ids") or [])],
                    "prompt_extra": lo.get("prompt_extra"),
                    "sort_order": lo.get("sort_order", 0),
                },
            )
            mapping[int(lo["id"])] = int(created["id"])
        return mapping

    async def _copy_files(
        self,
        src_id: int,
        new_id: int,
        scope_id: int,
        loadout_map: Dict[int, int],
        user_id: Optional[str],
    ) -> None:
        for f in await self.relations.list_files(src_id):
            if not await self.relations.resource_in_scope(
                int(f["resource_id"]), int(scope_id)
            ):
                continue
            old_lo = f.get("loadout_id")
            # ``.get`` rather than ``[...]``: every loadout of the source is in
            # the map, so a miss means the SOURCE row already pointed at a
            # foreign loadout (``attach_file`` refuses to create one). Copying
            # that dangling id forward would carry the inconsistency into the
            # new asset; the copy drops it instead.
            await self.relations.attach(
                new_id,
                int(f["resource_id"]),
                f["slot"],
                loadout_id=(
                    loadout_map.get(int(old_lo)) if old_lo is not None else None
                ),
                note=f.get("note"),
                attached_by=user_id,
                sort_order=f.get("sort_order", 0),
            )

    async def delete_asset(self, asset_id: int, scope_id: int) -> None:
        # soft_delete's scope predicate never matches a preset (scope_id NULL),
        # so without the gate it would return False and the caller would see a
        # silent no-op instead of "presets are read-only".
        await self._require_writable(asset_id, scope_id)
        await self.assets.soft_delete(int(asset_id), int(scope_id))

    # ── files ──────────────────────────────────────────────────────────────

    async def attach_file(
        self,
        asset_id: int,
        scope_id: int,
        req: AttachFileRequest,
        user_id: Optional[str],
    ) -> Dict[str, Any]:
        row = await self._require_writable(asset_id, scope_id)
        if not is_valid_slot(row["asset_type"], req.slot):
            raise AssetError(
                422,
                "invalid_slot",
                f"Slot '{req.slot}' is not valid for {row['asset_type']}",
            )
        if not await self.relations.resource_in_scope(
            int(req.resource_id), int(scope_id)
        ):
            raise AssetError(
                404, "resource_not_found", "Resource not found in this scope"
            )
        loadout_id = int(req.loadout_id) if req.loadout_id else None
        if loadout_id is not None:
            owned = {
                int(lo["id"])
                for lo in await self.relations.list_loadouts(int(asset_id))
            }
            if loadout_id not in owned:
                raise AssetError(
                    422, "loadout_mismatch", "Loadout does not belong to this asset"
                )
        f = await self.relations.attach(
            int(asset_id),
            int(req.resource_id),
            req.slot,
            loadout_id=loadout_id,
            note=req.note,
            attached_by=user_id,
        )
        await self.relations.touch_asset(int(asset_id))
        return _serialize_file(f)

    async def detach_file(
        self, asset_id: int, scope_id: int, resource_id: int, slot: str
    ) -> None:
        await self._require_writable(asset_id, scope_id)
        if not await self.relations.detach(int(asset_id), int(resource_id), slot):
            raise AssetError(
                404, "file_not_attached", "File is not attached to this slot"
            )
        await self.relations.touch_asset(int(asset_id))

    # ── links ──────────────────────────────────────────────────────────────

    async def add_link(
        self, asset_id: int, scope_id: int, req: LinkRequest
    ) -> Dict[str, Any]:
        src = await self._require_writable(asset_id, scope_id)
        dst = await self.assets.get(int(req.to_asset_id), int(scope_id))
        if not dst:
            raise AssetError(404, "asset_not_found", "Target asset not found")
        if not link_allowed(
            req.relation, src["asset_type"], src.get("subtype"), dst["asset_type"]
        ):
            raise AssetError(
                422,
                "link_not_allowed",
                f"{req.relation} is not allowed from "
                f"{src['asset_type']}/{src.get('subtype') or '-'} to {dst['asset_type']}",
            )
        link = await self.relations.add_link(
            int(asset_id), int(req.to_asset_id), req.relation
        )
        await self.relations.touch_asset(int(asset_id))
        return _serialize_link(link)

    async def remove_link(
        self, asset_id: int, scope_id: int, to_asset_id: int, relation: str
    ) -> None:
        await self._require_writable(asset_id, scope_id)
        if not await self.relations.remove_link(
            int(asset_id), int(to_asset_id), relation
        ):
            raise AssetError(404, "link_not_found", "Link not found")
        if relation == "wears":
            await self.relations.strip_from_loadouts(
                int(asset_id), costume_id=int(to_asset_id)
            )
        elif relation == "holds":
            await self.relations.strip_from_loadouts(
                int(asset_id), prop_id=int(to_asset_id)
            )
        await self.relations.touch_asset(int(asset_id))

    # ── loadouts ───────────────────────────────────────────────────────────

    async def _check_subset(
        self, asset_id: int, costume_ids: List[str], prop_ids: List[str]
    ) -> None:
        wears = await self.relations.link_targets(int(asset_id), "wears")
        holds = await self.relations.link_targets(int(asset_id), "holds")
        bad_c = [c for c in costume_ids if int(c) not in wears]
        bad_p = [p for p in prop_ids if int(p) not in holds]
        if bad_c or bad_p:
            raise AssetError(
                422,
                "loadout_not_subset",
                "Loadout may only reference costumes/props linked to this character",
                {"costume_ids": bad_c, "prop_ids": bad_p},
            )

    async def create_loadout(
        self, asset_id: int, scope_id: int, payload: LoadoutCreate
    ) -> Dict[str, Any]:
        row = await self._require_writable(asset_id, scope_id)
        if row["asset_type"] != "character":
            raise AssetError(
                422, "loadouts_character_only", "Only characters have loadouts"
            )
        await self._check_subset(asset_id, payload.costume_ids, payload.prop_ids)
        lo = await self.relations.create_loadout(
            int(asset_id),
            {
                "name": payload.name,
                "costume_ids": [int(c) for c in payload.costume_ids],
                "prop_ids": [int(p) for p in payload.prop_ids],
                "prompt_extra": payload.prompt_extra,
            },
        )
        await self.relations.touch_asset(int(asset_id))
        return _serialize_loadout(lo)

    async def update_loadout(
        self, asset_id: int, scope_id: int, loadout_id: int, payload: LoadoutUpdate
    ) -> Dict[str, Any]:
        await self._require_writable(asset_id, scope_id)
        fields = payload.model_dump(exclude_none=True)
        make_default = fields.pop("is_default", None)
        if "costume_ids" in fields or "prop_ids" in fields:
            await self._check_subset(
                asset_id, fields.get("costume_ids", []), fields.get("prop_ids", [])
            )
            if "costume_ids" in fields:
                fields["costume_ids"] = [int(c) for c in fields["costume_ids"]]
            if "prop_ids" in fields:
                fields["prop_ids"] = [int(p) for p in fields["prop_ids"]]
        lo = await self.relations.update_loadout(int(loadout_id), int(asset_id), fields)
        if not lo:
            raise AssetError(404, "loadout_not_found", "Loadout not found")
        if make_default:
            # False = not this asset's loadout, and nothing was written.
            if not await self.relations.set_default(int(loadout_id), int(asset_id)):
                raise AssetError(404, "loadout_not_found", "Loadout not found")
            lo = {**lo, "is_default": True}
        await self.relations.touch_asset(int(asset_id))
        return _serialize_loadout(lo)

    async def delete_loadout(
        self, asset_id: int, scope_id: int, loadout_id: int
    ) -> None:
        await self._require_writable(asset_id, scope_id)
        current = {
            int(lo["id"]): lo
            for lo in await self.relations.list_loadouts(int(asset_id))
        }
        if int(loadout_id) not in current:
            raise AssetError(404, "loadout_not_found", "Loadout not found")
        if current[int(loadout_id)]["is_default"]:
            raise AssetError(
                422, "cannot_delete_default", "Make another loadout default first"
            )
        await self.relations.delete_loadout(int(loadout_id), int(asset_id))
        await self.relations.touch_asset(int(asset_id))

    # ── prompt AI (translate / regenerate) ─────────────────────────────────

    async def translate_prompt(
        self,
        asset_id: int,
        scope_id: int,
        payload: PromptTranslateRequest,
        user_id: Optional[str],
    ) -> Dict[str, Any]:
        """Translate this asset's prompt into the other language (spec §5.1).

        Drives the user's assigned ``translation`` agent through the SAME
        callable the resources surface uses (``resource_ai_ops``), so both
        surfaces resolve the same provider, honour the same fallback pool and
        neutralize the prompt text the same way.

        Two rules the wire contract depends on:

        * a non-empty target is never overwritten unless ``force`` — and the
          skipped field is not even SENT, because paying for a provider call
          whose result is then discarded is the same bug, only quieter;
        * nothing left to do is a typed 422, not a 200 over an unchanged row.

        ``AllModelsFailed`` / ``LLMCallError`` become a 503 carrying the
        provider's own message. Any OTHER exception propagates: a defect of
        ours must not reach the user as "check your provider configuration".
        """
        row = await self._require_writable(asset_id, scope_id)
        plan = build_translate_plan(
            row, payload.target_lang, field_pairs=ASSET_PROMPT_FIELD_PAIRS
        )
        if not payload.force:
            plan = [p for p in plan if not (row.get(p[1]) or "").strip()]
        if not plan:
            # The two ways to get here need DIFFERENT next steps, so they get
            # different sentences: with ``force`` the targets were never
            # consulted, so pointing at force=true would be advice the caller
            # has already taken.
            raise AssetError(
                422,
                "nothing_to_translate",
                (
                    "Nothing to translate — both the positive and negative "
                    "prompts are empty on the source side"
                    if payload.force
                    else "Nothing to translate — the source prompt is empty, "
                    "or the target already has text (send force=true to "
                    "overwrite it)"
                ),
            )

        try:
            # No ``resource_id``: it lands verbatim in the ``agent_runs``
            # metadata, and an asset id filed under that key would be a wrong
            # answer to anyone tracing a run back to a resource.
            patch = await translate_fields(
                plan, target_lang=payload.target_lang, user_id=user_id
            )
        except Exception as exc:
            if not is_provider_failure(exc):
                raise
            raise AssetError(
                503,
                "translate_unavailable",
                str(exc) or "The translation agent is unavailable",
            )
        if not patch:
            raise AssetError(503, "translate_unavailable", _TRANSLATE_EMPTY)
        return await self._write_prompt(asset_id, scope_id, patch)

    async def regenerate_prompt(
        self, asset_id: int, scope_id: int, user_id: str
    ) -> Dict[str, Any]:
        """Reverse-engineer ``prompt_positive`` from the asset's primary file.

        The file is the lowest-``sort_order`` attachment in
        ``PRIMARY_SLOT[asset_type]`` — the same slot ``readiness`` calls this
        asset's defining image. Read from the slot table rather than
        re-declared here: a second copy of "a character's sheet" is how the
        two drift apart.

        Unlike the resources ``Generate prompt`` action (which dispatches the
        ``caption_asset`` DBOS workflow and answers with a task id), this runs
        the agent IN-REQUEST — the caller needs the written asset back, not a
        task to poll. See ``resource_ai_ops`` for that trade-off.
        """
        row = await self._require_writable(asset_id, scope_id)
        # Direct indexing, not ``.get``: asset_type is pinned by a DB CHECK and
        # by the schema Literal, so a miss is a code defect that must fail
        # loudly rather than resolve to a plausible-looking "not applicable".
        primary = PRIMARY_SLOT[row["asset_type"]]
        if primary is None:
            raise AssetError(
                422,
                "not_applicable",
                "Prompt assets have no image to reverse-engineer — their "
                "prompt IS the asset",
            )

        candidates = [
            f
            for f in await self.relations.list_files(int(asset_id))
            if f["slot"] == primary
        ]
        if not candidates:
            raise AssetError(
                422,
                "no_primary_file",
                f"Attach a file to the '{primary}' slot first",
            )
        # ``min`` is stable, so ties keep the repo's (slot, sort_order,
        # attached_at) ordering instead of an arbitrary one.
        resource_id = int(
            min(candidates, key=lambda f: f.get("sort_order") or 0)["resource_id"]
        )
        # attach_file scope-checked this resource once, at attach time. It can
        # have left the scope since, and this path hands its BYTES to a vision
        # model and writes the result onto the asset.
        if not await self.relations.resource_in_scope(resource_id, int(scope_id)):
            raise AssetError(
                404, "resource_not_found", "The primary file is no longer in this scope"
            )

        try:
            # ``user_id`` is required, not Optional like the translate path's:
            # the file read underneath is visibility-checked PER CALLER, and
            # there is no honest answer to "which caller" without one.
            caption = await caption_resource_for_caller(str(resource_id), user_id)
        except CaptionSourceUnavailable as exc:
            # "this file will never be captionable" — a 4xx the user can act
            # on, never the 503 that sends them to Settings → AI.
            raise AssetError(422, "file_not_captionable", str(exc))
        except CaptionAgentPaused as exc:
            # Its own code: "resume the caption agent" is a different action
            # from "the provider is unreachable", and the UI can only say so
            # if the two do not share a code.
            raise AssetError(503, "caption_paused", str(exc))
        except CaptionAgentFailed as exc:
            raise AssetError(503, "caption_unavailable", str(exc))
        except Exception as exc:
            if not is_provider_failure(exc):
                raise
            raise AssetError(
                503,
                "caption_unavailable",
                str(exc) or "The caption agent is unavailable",
            )

        patch: Dict[str, Any] = {}
        if caption.get("en"):
            patch["prompt_positive"] = caption["en"]
        if caption.get("zh"):
            patch["prompt_positive_zh"] = caption["zh"]
        if not patch:  # pragma: no cover - caption_resource_for_caller raises first
            raise AssetError(
                503,
                "caption_unavailable",
                "The caption agent returned no prompt — check the model "
                "assigned to Caption in Settings → AI",
            )
        return await self._write_prompt(asset_id, scope_id, patch)

    async def _write_prompt(
        self, asset_id: int, scope_id: int, patch: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Commit a prompt patch and answer with the DETAIL row.

        ``assets.update`` stamps ``updated_at`` itself (mig 445 ships no touch
        trigger), so no separate ``touch_asset`` — and the detail row is what
        both callers hand back: the client re-renders the asset it just had the
        agent rewrite, and a second round trip for it would be a gap the UI
        fills by guessing.
        """
        updated = await self.assets.update(int(asset_id), int(scope_id), patch)
        if not updated:
            # Soft-deleted (or moved out of scope) between _require_writable
            # and the UPDATE — the honest 404, not a TypeError in _derived.
            raise AssetError(404, "asset_not_found", "Asset not found")
        return await self.get_asset(int(asset_id), int(scope_id))

    # ── project refs ───────────────────────────────────────────────────────

    async def link_project(
        self, asset_id: int, scope_id: int, project_id: int, user_id: Optional[str]
    ) -> None:
        await self._require_writable(asset_id, scope_id)
        exists, team_id, owner_id = await self.relations.project_team_id(
            int(project_id)
        )
        if not exists:
            raise AssetError(404, "project_not_found", "Project not found")
        if team_id is None:
            # Personal project (projects.team_id NULL). Its asset scope is the
            # OWNER's personal team — the same resolution the read side
            # (GET /projects/{id}/assets) performs. Accepting it unchecked let a
            # collaborator link an asset from their own team into someone else's
            # personal project: a 201 for a row the read path can never return.
            team_id = await self._owner_personal_team(owner_id)
        if int(team_id) != int(scope_id):
            raise AssetError(
                422,
                "project_scope_mismatch",
                "Project belongs to a different team than this asset",
            )
        await self.relations.link_project(int(asset_id), int(project_id), user_id)
        await self.relations.touch_asset(int(asset_id))

    @staticmethod
    async def _owner_personal_team(owner_id: Optional[str]) -> int:
        """The personal team a team-less project belongs to, as a typed failure.

        ``_resolve_personal_team_id`` raises ValueError on legacy rows with no
        personal team; letting that out would be an untyped 500 on a path whose
        honest answer is "this project is not in your scope".
        """
        if owner_id is None:
            raise AssetError(
                422,
                "project_scope_mismatch",
                "Project has no team and no owner to resolve a scope from",
            )
        try:
            return int(await _resolve_personal_team_id(str(owner_id)))
        except ValueError:
            raise AssetError(
                422,
                "project_scope_mismatch",
                "Project owner has no personal team; cannot resolve its scope",
            )

    async def unlink_project(
        self, asset_id: int, scope_id: int, project_id: int
    ) -> None:
        await self._require_writable(asset_id, scope_id)
        if not await self.relations.unlink_project(int(asset_id), int(project_id)):
            raise AssetError(
                404, "project_ref_not_found", "Asset is not linked to this project"
            )
        await self.relations.touch_asset(int(asset_id))
