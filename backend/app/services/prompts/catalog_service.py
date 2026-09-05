"""Unified prompt catalog (spec 2026-09-05 §3.1): templates ∪ prompted pictures.

Filtering by form / origin / q happens in Python AFTER the counts are taken, so
the chips describe the whole segment ("Images 12") rather than the current
narrowing. The corpus is small by construction (one scope's prompts); if it
ever is not, the count queries move to SQL and this comment goes with them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db.scope import system_request_scope
from app.repositories.assets_repository import AssetsRepository
from app.repositories.prompt_catalog_repository import PromptCatalogRepository
from app.services.assets.assets_service import AssetError
from app.services.prompts.entries import (
    entry_from_asset,
    entry_from_resource,
    matches_query,
    sort_entries,
)

FORMS = ("template", "image", "album")
ORIGINS = ("typed", "extracted", "captioned")
_TEMPLATE_PAGE = 500  # every template in the scope; the shelf paginates in Python


class PromptCatalogService:
    def __init__(
        self,
        assets_repo: Optional[AssetsRepository] = None,
        catalog_repo: Optional[PromptCatalogRepository] = None,
    ):
        self.assets = assets_repo or AssetsRepository()
        self.catalog = catalog_repo or PromptCatalogRepository()

    async def _templates(
        self, scope_id: int, *, segment: str, project_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        rows = await self.assets.list(
            scope_id,
            asset_type="prompt",
            project_id=project_id if segment == "project" else None,
            library="all" if segment == "project" else "in",
            sort="recent",
            limit=_TEMPLATE_PAGE,
            offset=0,
        )
        if segment == "system":
            rows = [r for r in rows if r.get("is_system_preset")]
        else:
            rows = [r for r in rows if not r.get("is_system_preset")]
        examples = await self.catalog.example_file_ids([int(r["id"]) for r in rows])
        return [
            entry_from_asset(r, example_resource_ids=examples.get(int(r["id"]), []))
            for r in rows
        ]

    async def _pictures(
        self, scope_id: int, *, segment: str, project_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        if segment == "system":
            return []
        async with system_request_scope(
            "prompt catalog: scope membership gated by /prompts"
        ):
            rows = await self.catalog.list_prompted_resources(
                scope_id, project_id=project_id if segment == "project" else None
            )
        return [entry_from_resource(r) for r in rows]

    async def _segment(
        self, scope_id: int, segment: str, project_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        if segment == "project" and project_id is None:
            raise AssetError(
                422, "project_required", "segment=project needs project_id"
            )
        templates = await self._templates(
            scope_id, segment=segment, project_id=project_id
        )
        pictures = await self._pictures(
            scope_id, segment=segment, project_id=project_id
        )
        return sort_entries(templates + pictures)

    async def list(
        self,
        scope_id: int,
        *,
        segment: str,
        project_id: Optional[int],
        form: Optional[str],
        origin: Optional[str],
        q: Optional[str],
        limit: int,
        offset: int,
    ) -> Dict[str, Any]:
        """One page of a segment.

        All three counters — ``total``, ``by_form``, ``by_origin`` — describe the
        WHOLE segment; only ``items`` is narrowed by form / origin / q and then
        paginated (ruling R6).
        """
        entries = await self._segment(scope_id, segment, project_id)
        by_form = {f: sum(1 for e in entries if e["form"] == f) for f in FORMS}
        by_origin = {o: sum(1 for e in entries if e["origin"] == o) for o in ORIGINS}
        narrowed = [
            e
            for e in entries
            if (form is None or e["form"] == form)
            and (origin is None or e["origin"] == origin)
            and matches_query(e, q or "")
        ]
        # ``total`` is the segment, not the page: the shelf's "All N" chip and
        # the "truly empty vs. no match" decision both read it that way, and a
        # narrowed count would make an empty result indistinguishable from an
        # empty library (ruling R6).
        return {
            "items": narrowed[offset : offset + limit],
            "total": len(entries),
            "by_form": by_form,
            "by_origin": by_origin,
        }

    async def counts(
        self, scope_id: int, *, project_id: Optional[int]
    ) -> Dict[str, Any]:
        mine = len(await self._segment(scope_id, "mine", None))
        system = len(await self._segment(scope_id, "system", None))
        project = (
            len(await self._segment(scope_id, "project", project_id))
            if project_id
            else None
        )
        return {"mine": mine, "project": project, "system": system}
