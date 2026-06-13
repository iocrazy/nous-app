"""Outpaint derive service (Phase 3 Day 13).

Extends a source image's canvas with the blur-fill primitive and
persists the result as a new sibling resource via the shared derive
pipeline.

``prompt`` is accepted and logged but does not affect the v1 fill —
it is the seam for the AI outpaint path: once nous-center ships an
outpaint workflow, this service routes prompt + padding there and the
editor needs no changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.repositories.resources_repository import ResourcesRepository
from app.services.canvas.derive_persistence import (
    DeriveError,
    ResourceRepoProtocol,
    load_source_image,
    persist_derived_image,
)
from app.services.canvas.image_outpaint import (
    OutpaintError,
    Padding,
    extend_canvas,
)

logger = logging.getLogger(__name__)

# Same (status_code, detail) error contract as the other derive services.
OutpaintDeriveError = DeriveError


@dataclass(frozen=True)
class OutpaintDeriveResult:
    resource: dict


def _outpaint_filename(source_filename: str, override: Optional[str]) -> str:
    if override:
        return override
    if not source_filename:
        return "outpaint.png"
    return f"outpaint-{source_filename}"


async def derive_outpaint_resource(
    *,
    source_resource_id: str,
    user_id: str,
    padding: Padding,
    prompt: Optional[str] = None,
    filename_override: Optional[str] = None,
    repo: Optional[ResourceRepoProtocol] = None,
) -> OutpaintDeriveResult:
    """Extend ``source_resource_id`` by ``padding`` and persist as a new
    sibling resource in the same scope.

    Raises:
        OutpaintDeriveError: with the HTTP status the router should
            surface (404 source missing, 400 invalid source / padding,
            500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    source = await load_source_image(repo, source_resource_id)
    try:
        extended = extend_canvas(source.file_bytes, padding, mime_type=source.mime_type)
    except OutpaintError as exc:
        raise OutpaintDeriveError(status_code=400, detail=str(exc)) from exc

    if prompt:
        # v1 fill is deterministic — record the intent so the AI path's
        # rollout can be compared against real demand.
        logger.info(
            "outpaint prompt collected (AI path pending) source=%s len=%d",
            source_resource_id,
            len(prompt),
        )

    new_resource = await persist_derived_image(
        repo,
        user_id=user_id,
        scope_id=source.scope_id,
        folder_id=source.folder_id,
        library_id=source.library_id,
        filename=_outpaint_filename(source.filename, filename_override),
        image_bytes=extended,
        mime_type=source.mime_type,
    )
    return OutpaintDeriveResult(resource=new_resource)
