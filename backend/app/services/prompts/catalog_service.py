"""Unified prompt catalog (spec 2026-09-05 §3.1): templates ∪ prompted pictures.

Filtering by form / origin / q happens in Python AFTER the counts are taken, so
the chips describe the whole segment ("Images 12") rather than the current
narrowing. The corpus is bounded by construction: templates are paged to
``_TEMPLATE_CEILING`` and pictures to ``_PICTURE_CEILING`` (2000 each). Hitting
either ceiling TRUNCATES the list and is logged at WARNING — a silently short
shelf reads exactly like a small library, which is the failure this bound
exists to make visible. If a real scope reaches a ceiling, the counting and
filtering move to SQL and these constants go with them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

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
# The repository CLAMPS its own limit to 200 (``assets_repository.py:409``:
# ``limit = max(1, min(int(limit), 200))``), so asking for more than that in
# one call silently returns 200. The page size is therefore the real cap and
# the shelf is assembled by paging up to a ceiling.
_TEMPLATE_PAGE = 200
_TEMPLATE_CEILING = 2000
_PICTURE_CEILING = 2000


class PromptCatalogService:
    def __init__(
        self,
        assets_repo: Optional[AssetsRepository] = None,
        catalog_repo: Optional[PromptCatalogRepository] = None,
    ):
        self.assets = assets_repo or AssetsRepository()
        self.catalog = catalog_repo or PromptCatalogRepository()

    async def _templates(
        self,
        scope_id: int,
        *,
        segment: str,
        project_id: Optional[int],
        with_thumbs: bool = True,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        truncated = False
        page_index = 0
        while True:
            page = await self.assets.list(
                scope_id,
                asset_type="prompt",
                project_id=project_id if segment == "project" else None,
                library="all" if segment == "project" else "in",
                sort="recent",
                limit=_TEMPLATE_PAGE,
                offset=page_index * _TEMPLATE_PAGE,
            )
            rows.extend(page)
            page_index += 1
            if len(page) < _TEMPLATE_PAGE:  # the last page is a short one
                break
            if len(rows) >= _TEMPLATE_CEILING:
                truncated = True
                break
        if truncated:
            logger.warning(
                "[prompt-catalog] template ceiling {} reached for scope {} "
                "(segment={}) — list is truncated",
                _TEMPLATE_CEILING,
                scope_id,
                segment,
            )
        if segment == "system":
            rows = [r for r in rows if r.get("is_system_preset")]
        else:
            rows = [r for r in rows if not r.get("is_system_preset")]
        # ``counts()`` only ever takes ``len()`` of the result, so the thumbnail
        # lookup is pure cost there — one query per segment per request.
        examples = (
            await self.catalog.example_file_ids([int(r["id"]) for r in rows])
            if with_thumbs
            else {}
        )
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
                scope_id,
                project_id=project_id if segment == "project" else None,
                limit=_PICTURE_CEILING,
            )
        if len(rows) >= _PICTURE_CEILING:
            logger.warning(
                "[prompt-catalog] picture ceiling {} reached for scope {} "
                "(segment={}) — list is truncated",
                _PICTURE_CEILING,
                scope_id,
                segment,
            )
        return [entry_from_resource(r) for r in rows]

    async def _segment(
        self,
        scope_id: int,
        segment: str,
        project_id: Optional[int],
        *,
        with_thumbs: bool = True,
    ) -> List[Dict[str, Any]]:
        if segment == "project" and project_id is None:
            raise AssetError(
                422, "project_required", "segment=project needs project_id"
            )
        templates = await self._templates(
            scope_id, segment=segment, project_id=project_id, with_thumbs=with_thumbs
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
        mine = len(await self._segment(scope_id, "mine", None, with_thumbs=False))
        system = len(await self._segment(scope_id, "system", None, with_thumbs=False))
        project = (
            len(await self._segment(scope_id, "project", project_id, with_thumbs=False))
            if project_id
            else None
        )
        return {"mine": mine, "project": project, "system": system}
