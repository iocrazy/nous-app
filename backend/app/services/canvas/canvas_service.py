"""Canvas service (Phase 1 of canvas + AI upgrade).

Holds the business logic that the API router shouldn't carry:
- optimistic-lock comparison + conflict signalling
- normalising the JSONB pass-through fields

Project-membership gating lives at the route layer via
``ensure_project_membership`` — we do not re-check it here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.canvas_asset_refs_repository import CanvasAssetRefsRepository
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.repositories.canvas_repository import CanvasRepository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.schemas.canvas import CanvasCreate, CanvasUpdate
from app.services.canvas.asset_node_refs import extract_asset_node_refs
from app.services.canvas.asset_refs import (
    extract_asset_refs,
    extract_output_generation_ids,
    merge_promoted_output_refs,
)


class CanvasConflict(Exception):
    """Raised when an update's ``base_updated_at`` no longer matches the
    server row. The router catches this and returns 409 with the
    current server state.
    """

    def __init__(self, current_row: Dict[str, Any]) -> None:
        super().__init__("canvas optimistic-lock conflict")
        self.current = current_row


class CanvasService:
    def __init__(
        self,
        repository: Optional[CanvasRepository] = None,
        refs_repository: Optional[CanvasRefsRepository] = None,
        asset_refs_repository: Optional[CanvasAssetRefsRepository] = None,
        generated_media_repository: Optional[GeneratedMediaRepository] = None,
    ) -> None:
        self.repo = repository or CanvasRepository()
        self.refs_repo = refs_repository or CanvasRefsRepository()
        self.asset_refs_repo = asset_refs_repository or CanvasAssetRefsRepository()
        self.gen_repo = generated_media_repository or GeneratedMediaRepository()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        return await self.repo.get_by_id(canvas_id)

    async def list_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_for_project(project_id)

    async def list_trashed_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_trashed_for_project(project_id)

    async def get_project_id(self, canvas_id: str) -> Optional[str]:
        return await self.repo.get_project_id(canvas_id)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_in_project(
        self,
        project_id: str,
        data: CanvasCreate,
        created_by: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return await self.repo.create(
            project_id=project_id,
            name=data.name,
            kind=data.kind,
            created_by=created_by,
            viewport_json=data.viewport_json,
            # Whether the asset may be referenced from this project is decided at
            # the route layer, with the project guards — same split as
            # project-membership gating (see the module docstring).
            asset_id=int(data.asset_id) if data.asset_id else None,
        )

    async def peek_storyboard(
        self, project_id: str, episode_id: str
    ) -> Optional[Dict[str, Any]]:
        """Existence check for the get-or-create storyboard flow's read/write
        gate split (2026-08-12 fix): the router calls this FIRST to decide
        which access guard to run — an existing canvas is a pure read
        (viewers may fetch it); a missing one means the GET is about to
        CREATE a row, so it must pass the write gate a POST would."""
        return await self.repo.get_storyboard_canvas(project_id, episode_id)

    async def get_or_create_storyboard(
        self,
        *,
        project_id: str,
        episode_id: str,
        name: str,
        created_by: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Idempotent get-or-create for an episode's system storyboard
        canvas (shot-nodes-on-canvas spec 2026-08-11 §2). The real
        idempotence guarantee lives in the DB's partial unique index
        (``CanvasRepository.create_storyboard_canvas`` re-reads on a
        concurrent 23505) — this method's get-then-create is just the
        common-case fast path, not the source of truth for "only one".
        """
        existing = await self.repo.get_storyboard_canvas(project_id, episode_id)
        if existing:
            return existing
        return await self.repo.create_storyboard_canvas(
            project_id=project_id,
            episode_id=episode_id,
            name=name,
            created_by=created_by,
        )

    async def update_with_lock(
        self,
        canvas_id: str,
        data: CanvasUpdate,
    ) -> Dict[str, Any]:
        """Apply an optimistic-locked PUT.

        Returns the updated row on success. Raises ``CanvasConflict`` with
        the current server row when the lock token mismatches. Raises
        ``LookupError`` when the canvas doesn't exist.
        """
        current = await self.repo.get_by_id(canvas_id)
        if current is None:
            raise LookupError(f"canvas {canvas_id} not found")

        client_token = data.base_updated_at.isoformat()
        server_token = str(current.get("base_updated_at") or "")
        if not _tokens_match(client_token, server_token):
            logger.info(
                "canvas %s optimistic-lock conflict (client=%s server=%s)",
                canvas_id,
                client_token,
                server_token,
            )
            raise CanvasConflict(current)

        fields = data.model_dump(
            exclude={"base_updated_at"},
            exclude_none=True,
        )

        updated = await self.repo.update_with_lock(
            canvas_id,
            expected_base_updated_at=server_token,
            fields=fields,
        )
        if updated is None:
            # Race: someone else updated between our read and write.
            fresh = await self.repo.get_by_id(canvas_id)
            raise CanvasConflict(fresh or current)

        # Only recompute refs when nodes_json was part of this save.
        if "nodes_json" in fields:
            await self._sync_refs(canvas_id, fields["nodes_json"])
        return updated

    async def _sync_refs(self, canvas_id: str, nodes_json: Any) -> None:
        """Recompute BOTH derived ref mirrors from nodes_json.

        Non-fatal, and independently so. Each mirror gets its OWN
        try/except: both tables are rebuildable from ``nodes_json`` (see the
        two backfill scripts), so a failure must never break the canvas save
        the user just performed — and a failure in one must not skip the
        other. Sharing one ``try`` would have made the asset mirror silently
        conditional on the resource mirror succeeding, which is the
        "one flag's reporting nested inside another's branch" shape CLAUDE.md's
        defensive-patterns section forbids.

        ``except Exception`` + ``logger.error`` — contained AND recorded, not
        ``except: pass``.
        """
        try:
            refs = extract_asset_refs(nodes_json)
            refs = await self._with_archived_outputs(canvas_id, nodes_json, refs)
            await self.refs_repo.replace_for_canvas(canvas_id, refs)
        except Exception as e:  # noqa: BLE001 — contained, logged, non-fatal
            logger.error(
                f"canvas {canvas_id} resource-refs sync failed (non-fatal): {e}"
            )

        try:
            asset_refs, skipped = extract_asset_node_refs(nodes_json)
            if skipped:
                # A node that names an asset we cannot read is a REPORTED
                # drop, never a silent one: the ref is gone from the mirror
                # and only this line says so.
                logger.warning(
                    f"canvas {canvas_id}: {skipped} asset node(s) skipped "
                    "(asset_id absent or not a snowflake)"
                )
            disowned = await self.asset_refs_repo.replace_for_canvas(
                canvas_id, asset_refs
            )
            if disowned:
                # A node paired an asset with another asset's loadout. The ref
                # is kept with loadout_id NULL (the asset IS on the canvas);
                # only this line says the costume half was refused.
                logger.warning(
                    f"canvas {canvas_id}: {disowned} asset ref(s) stored without "
                    "their loadout (the loadout belongs to a different asset)"
                )
        except Exception as e:  # noqa: BLE001 — contained, logged, non-fatal
            logger.error(f"canvas {canvas_id} asset-refs sync failed (non-fatal): {e}")

    async def _with_archived_outputs(
        self, canvas_id: str, nodes_json: Any, refs: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """``refs`` plus the output refs whose generation has been archived.

        Its own try: a failed lookup degrades to the legacy refs and says so —
        it must not stop them being written.
        """
        pairs = extract_output_generation_ids(nodes_json)
        if not pairs:
            return refs
        try:
            promoted = await self.gen_repo.promoted_resource_ids(
                gen_id for _, gen_id in pairs
            )
        except Exception as e:  # noqa: BLE001 — contained, logged, non-fatal
            logger.error(
                f"canvas {canvas_id} archived-output ref lookup failed "
                f"(non-fatal, legacy refs only): {e}"
            )
            return refs
        return merge_promoted_output_refs(refs, pairs, promoted)

    async def soft_delete(self, canvas_id: str) -> bool:
        return await self.repo.soft_delete(canvas_id)

    async def restore(self, canvas_id: str) -> bool:
        return await self.repo.restore(canvas_id)

    async def purge(self, canvas_id: str) -> bool:
        return await self.repo.purge(canvas_id)


def _tokens_match(client: str, server: str) -> bool:
    """Tolerant timestamp comparison.

    Supabase + PostgREST round-trip timestamps as ``+00:00`` or trailing
    ``Z`` interchangeably, sometimes drop trailing zeros on microseconds,
    and the client might have re-serialised through JS. We trim both
    sides to a canonical second-precision string before comparing —
    millisecond-tier collisions on a single canvas are virtually
    impossible in practice and we'd rather not 409 on a cosmetic format
    diff.
    """
    return _canonical(client) == _canonical(server)


def _canonical(ts: str) -> str:
    if not ts:
        return ""
    s = ts.replace("Z", "+00:00").strip()
    # Truncate any fractional seconds — supabase returns 6-digit micros,
    # Pydantic emits the same; some clients downcast to ms. Aligning at
    # the second boundary side-steps any of that.
    if "." in s:
        before, _, after = s.partition(".")
        # keep the offset suffix that follows the fractional portion
        for sep in ("+", "-"):
            idx = after.find(sep)
            if idx != -1:
                return f"{before}{after[idx:]}"
        return before
    return s
