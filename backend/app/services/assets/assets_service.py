"""AssetsService — the invariants layer (spec §3, §7).

Repos are dumb; this is where slot validity, link-type rules, loadout ⊆ links,
default-loadout-on-character, 409-on-duplicate and system-preset read-only are
enforced. Every failure is a typed AssetError the router maps 1:1 to HTTP —
no silent no-ops (CLAUDE.md "触发路径必须类型化失败回显").
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
)
from app.services.assets.slots import is_valid_slot, link_allowed
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
