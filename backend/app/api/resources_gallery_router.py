# backend/app/api/resources_gallery_router.py

"""
Resources Gallery Router

Gallery-as-a-first-class-entity endpoints (PR-A). A gallery is one
``resources`` row (file_type='gallery',
mime_type='application/x-mediahub-gallery') whose child images are ordinary
resources linked through the ``gallery_items`` junction. Children are hidden
from the normal library listing so a gallery reads as a single tile.

Endpoints:
- POST /resources/galleries                — create a gallery entity
- PUT  /resources/{id}/gallery-items       — set the ordered children
- GET  /resources/{id}/gallery-items       — read the ordered children

This router MUST be included BEFORE resources_crud_router: the CRUD router has
a catch-all ``GET /resources/{resource_id}`` that would otherwise be a peer to
these routes (the extra ``/gallery-items`` segment keeps GET distinct, but
registering first is the defensive default).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.api.media_permissions import check_media_access
from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.core.scope_guards import verify_scope_access
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources import GallerySetItemsRequest

GALLERY_MIME = "application/x-mediahub-gallery"

# All endpoints require auth (AuthDep) and are resources-dedicated → establish
# the ambient tenant Scope at the ROUTER level (mirrors resources_upload_router).
router = APIRouter(prefix="/resources", dependencies=[Depends(scoped_request)])


@router.post("/galleries")
async def create_gallery(
    auth: AuthDep,
    scope_id: str = Query(..., description="Owning workspace scope (snowflake id)"),
    filename: str = Query(..., min_length=1, max_length=500),
    folder_id: Optional[str] = Query(None),
    _scope_guard: None = Depends(verify_scope_access),
):
    """Create an empty gallery entity in ``scope_id``.

    Creates one ``resources`` row (the gallery) plus a ``resource_items`` row
    linking it into the scope/folder. Children are attached separately via
    PUT /resources/{id}/gallery-items.
    """
    try:
        repo = ResourcesRepository()
        resource_data = {
            "creator_id": auth.user_id,
            "source_type": "upload",
            "filename": filename,
            "file_type": "gallery",
            "mime_type": GALLERY_MIME,
            "current_version": 1,
        }
        gallery = await repo.create_resource(resource_data)
        gallery_id = str(gallery["id"])

        item_data = {
            "resource_id": gallery_id,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "added_by": auth.user_id,
        }
        await repo.create_resource_item(item_data)

        return {"success": True, "data": gallery}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create gallery: {e}")
        raise HTTPException(status_code=500, detail="Failed to create gallery")


@router.get("/gallery-membership")
async def get_gallery_membership(
    auth: AuthDep,
    scope_id: str = Query(..., description="Owning workspace scope (snowflake id)"),
    _scope_guard: None = Depends(verify_scope_access),
):
    """Gallery membership for one scope, for the direct-query library list.

    ``gallery_items`` is service-role only (RLS, mig 375) so the frontend's
    supabase-js list cannot read it. This exposes the two derived bits the list
    needs: the child image ids to hide (so a gallery reads as one tile) and the
    per-gallery child counts (the ▣ badge). Both are small; the frontend caches
    the result per scope and filters/annotates client-side.

    Response::

        {"success": true, "data": {
            "child_image_ids": ["..."],
            "gallery_counts": {"<gallery_id>": 3}
        }}
    """
    try:
        repo = ResourcesRepository()
        data = await repo.get_scope_gallery_membership(scope_id)
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get gallery membership for scope {scope_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get gallery membership")


@router.put("/{gallery_id}/gallery-items")
async def set_gallery_items(
    gallery_id: str,
    body: GallerySetItemsRequest,
    auth: AuthDep,
    scope_id: str = Query(..., description="Scope the gallery + images belong to"),
    _scope_guard: None = Depends(verify_scope_access),
):
    """Replace a gallery's ordered children with ``body.image_ids``.

    Validates the gallery is accessible to the caller and that every proposed
    child is an image resource in ``scope_id``. Copies the first child's
    thumbnail onto the gallery row so the grid tile shows a cover.
    """
    try:
        repo = ResourcesRepository()

        gallery = await repo.get_resource_by_id(gallery_id)
        if not gallery or gallery.get("mime_type") != GALLERY_MIME:
            raise HTTPException(status_code=404, detail="Gallery not found")
        if not await check_media_access(gallery_id, auth.user_id, None):
            raise HTTPException(status_code=403, detail="Access denied")

        image_ids = [i for i in body.image_ids if i]
        # De-dup while preserving order (junction PK forbids repeats anyway).
        seen: set = set()
        ordered_ids = []
        for i in image_ids:
            if i not in seen:
                seen.add(i)
                ordered_ids.append(i)

        if ordered_ids:
            valid = await repo.validate_scope_image_ids(scope_id, ordered_ids)
            invalid = [i for i in ordered_ids if i not in valid]
            if invalid:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Some ids are not image resources in this scope: " f"{invalid}"
                    ),
                )

        await repo.set_gallery_items(gallery_id, ordered_ids)

        # Cover: copy the first child's thumbnail onto the gallery row so the
        # library tile previews. Clearing to None when the gallery is emptied.
        cover_source: Optional[str] = None
        if ordered_ids:
            first = await repo.get_resource_by_id(ordered_ids[0])
            if first:
                cover_source = first.get("thumbnail_path") or first.get("file_path")
        await repo.update_resource(gallery_id, {"thumbnail_path": cover_source})

        items = await repo.get_gallery_items(gallery_id)
        return {"success": True, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set gallery items for {gallery_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to set gallery items")


@router.get("/{gallery_id}/gallery-items")
async def list_gallery_items(
    gallery_id: str,
    auth: AuthDep,
):
    """Ordered child images of a gallery (creator or team member only)."""
    try:
        repo = ResourcesRepository()
        gallery = await repo.get_resource_by_id(gallery_id)
        if not gallery or gallery.get("mime_type") != GALLERY_MIME:
            raise HTTPException(status_code=404, detail="Gallery not found")
        if not await check_media_access(gallery_id, auth.user_id, None):
            raise HTTPException(status_code=403, detail="Access denied")

        items = await repo.get_gallery_items(gallery_id)
        return {"success": True, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list gallery items for {gallery_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list gallery items")
