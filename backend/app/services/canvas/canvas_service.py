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

from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.repositories.canvas_repository import CanvasRepository
from app.schemas.canvas import CanvasCreate, CanvasUpdate
from app.services.canvas.asset_refs import extract_asset_refs


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
    ) -> None:
        self.repo = repository or CanvasRepository()
        self.refs_repo = refs_repository or CanvasRefsRepository()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        return await self.repo.get_by_id(canvas_id)

    async def list_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_for_project(project_id)

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
        """Recompute canvas_resource_refs from nodes_json. Non-fatal:
        the refs table is rebuildable, so a failure here must never break
        the canvas save the user just performed."""
        try:
            refs = extract_asset_refs(nodes_json)
            await self.refs_repo.replace_for_canvas(canvas_id, refs)
        except Exception as e:  # noqa: BLE001 — deliberately swallow
            logger.warning(f"canvas {canvas_id} refs sync failed (non-fatal): {e}")

    async def delete(self, canvas_id: str) -> bool:
        return await self.repo.delete(canvas_id)


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
