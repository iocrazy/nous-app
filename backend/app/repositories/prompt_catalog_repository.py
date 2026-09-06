"""ORM statements behind the unified prompt catalog (spec 2026-09-05 §3.1).

Templates come from ``AssetsRepository.list`` (unchanged); this repository owns
the OTHER half — resources whose rows carry prompt text — plus the example-file
lookup that gives a template its thumbnails.

Scope: callers run these inside ``system_request_scope`` (membership is gated
at the router). Under a plain user scope the ``Resources`` SELECT would get
``creator_id = me`` injected and a teammate's uploads would vanish silently.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.db.session import read_scope
from app.models.assets import AssetFiles
from app.models.canvas import Canvases, CanvasResourceRefs
from app.models.media import ResourceItems, Resources
from app.repositories.media_repository import has_prompt_expr

_EXAMPLES_PER_ASSET = 3
# Safety net only — the catalog service passes its own ceiling explicitly.
# An UNBOUNDED read here would materialize every prompted resource in the
# scope on every request, so the default is a bound, not None.
_DEFAULT_PICTURE_LIMIT = 2000

_COLUMNS = (
    Resources.id,
    Resources.filename,
    Resources.media_id,
    Resources.gen_prompt,
    Resources.gen_prompt_zh,
    Resources.gen_prompt_negative,
    Resources.gen_prompt_negative_zh,
    Resources.gen_params,
    Resources.slide_prompts,
    Resources.prompt_origin,
    Resources.updated_at,
)


class PromptCatalogRepository:
    def _prompted_resources_stmt(
        self,
        scope_id: int,
        *,
        project_id: Optional[int],
        limit: int = _DEFAULT_PICTURE_LIMIT,
    ):
        stmt = (
            select(*_COLUMNS)
            .join(ResourceItems, ResourceItems.resource_id == Resources.id)
            .where(ResourceItems.scope_id == int(scope_id))
            .where(Resources.is_trashed.is_(False))
            .where(has_prompt_expr())
            .distinct()
        )
        if project_id is not None:
            # "This project" for a picture = referenced by one of its canvases
            # (spec §8 — not "uploaded into its library").
            referenced = (
                select(CanvasResourceRefs.resource_id)
                .join(Canvases, Canvases.id == CanvasResourceRefs.canvas_id)
                .where(Canvases.project_id == int(project_id))
            )
            stmt = stmt.where(Resources.id.in_(referenced))
        return stmt.order_by(Resources.updated_at.desc()).limit(int(limit))

    async def list_prompted_resources(
        self,
        scope_id: int,
        *,
        project_id: Optional[int] = None,
        limit: int = _DEFAULT_PICTURE_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Prompted resources, newest first, capped at ``limit``.

        A caller that gets exactly ``limit`` rows back cannot tell a full scope
        from a truncated one — the catalog service compares the length against
        its own ceiling and logs when they meet.
        """
        stmt = self._prompted_resources_stmt(
            scope_id, project_id=project_id, limit=limit
        )
        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    def _example_files_stmt(self, asset_ids: List[int]):
        return (
            select(AssetFiles.asset_id, AssetFiles.resource_id)
            .where(AssetFiles.asset_id.in_([int(a) for a in asset_ids]))
            .where(AssetFiles.slot == "examples")
            .order_by(
                AssetFiles.asset_id, AssetFiles.sort_order, AssetFiles.attached_at
            )
        )

    async def example_file_ids(self, asset_ids: List[int]) -> Dict[int, List[int]]:
        if not asset_ids:
            return {}
        async with read_scope() as session:
            rows = (await session.execute(self._example_files_stmt(asset_ids))).all()
        out: Dict[int, List[int]] = {}
        for asset_id, resource_id in rows:
            bucket = out.setdefault(int(asset_id), [])
            if len(bucket) < _EXAMPLES_PER_ASSET:
                bucket.append(int(resource_id))
        return out
