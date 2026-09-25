"""API routes for Smart Collections."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import AuthDep
from app.repositories.collections_repository import get_collections_repository
from app.schemas.collections import (
    CollectionCreate,
    CollectionInitPresetsResult,
    CollectionListResponse,
    CollectionMediaResponse,
    CollectionRefreshResult,
    CollectionResponse,
    CollectionUpdate,
)
from app.services.library.collections_service import CollectionsService

router = APIRouter(prefix="/collections", tags=["Collections"])

_DEFAULT_RULES = {"match": "all", "conditions": []}


def _collection_out(row: dict[str, Any]) -> dict[str, Any]:
    """Project a repository row onto ``CollectionResponse``.

    Every ``smart_collections`` column except ``id``/``user_id``/``name``/
    ``rules`` is nullable, and the repository returns the key with ``None``
    rather than omitting it, so ``row.get(key, default)`` never applied the
    default. NULLs fall back here instead, and a legacy row still returns 200.
    """
    created_at = row.get("created_at")
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "icon": row.get("icon") or "📁",
        "color": row.get("color"),
        "description": row.get("description"),
        "rules": row.get("rules") or _DEFAULT_RULES,
        "media_count": row.get("cached_count") or 0,
        "cached_at": row.get("cached_at"),
        "is_preset": bool(row.get("is_preset")),
        "is_active": row.get("is_active") is not False,
        "sort_by": row.get("sort_by") or "created_at",
        "sort_order": row.get("sort_order") or "desc",
        "created_at": created_at,
        "updated_at": row.get("updated_at") or created_at,
    }


@router.get("", response_model=CollectionListResponse)
async def list_collections(
    auth: AuthDep,
    include_presets: bool = Query(True, description="Include preset collections"),
):
    """
    List all smart collections for the current user.

    Includes both user-created and preset collections by default.
    """
    repo = get_collections_repository()
    collections = await repo.get_all_collections(auth.user_id)

    if not include_presets:
        collections = [c for c in collections if not c.get("is_preset")]

    return CollectionListResponse(
        collections=[_collection_out(c) for c in collections],
        total=len(collections),
    )


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    auth: AuthDep,
    collection: CollectionCreate,
):
    """
    Create a new smart collection.

    Define rules to automatically match videos based on tags, author, date, etc.
    """
    repo = get_collections_repository()

    created = await repo.create_collection(
        user_id=auth.user_id,
        name=collection.name,
        icon=collection.icon,
        description=collection.description,
        rules=collection.rules.model_dump(),
        sort_by=collection.sort_by,
        sort_order=collection.sort_order,
    )

    return _collection_out(created)


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection(
    auth: AuthDep,
    collection_id: str,
):
    """Get a specific collection by ID."""
    repo = get_collections_repository()
    collection = await repo.get_collection_by_id(collection_id, auth.user_id)

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    return _collection_out(collection)


@router.put("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    auth: AuthDep,
    collection_id: str,
    update: CollectionUpdate,
):
    """
    Update a collection.

    Preset collections can only have their rules updated, not name/icon.
    """
    repo = get_collections_repository()

    existing = await repo.get_collection_by_id(collection_id, auth.user_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    update_data = {}
    if update.name is not None and not existing.get("is_preset"):
        update_data["name"] = update.name
    if update.icon is not None and not existing.get("is_preset"):
        update_data["icon"] = update.icon
    if update.description is not None:
        update_data["description"] = update.description
    if update.rules is not None:
        update_data["rules"] = update.rules.model_dump()
    if update.sort_by is not None:
        update_data["sort_by"] = update.sort_by
    if update.sort_order is not None:
        update_data["sort_order"] = update.sort_order

    updated = await repo.update_collection(collection_id, auth.user_id, **update_data)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update collection",
        )

    return _collection_out(updated)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    auth: AuthDep,
    collection_id: str,
):
    """
    Delete a collection.

    Preset collections cannot be deleted.
    """
    repo = get_collections_repository()

    existing = await repo.get_collection_by_id(collection_id, auth.user_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    if existing.get("is_preset"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preset collections cannot be deleted",
        )

    deleted = await repo.delete_collection(collection_id, auth.user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete collection",
        )


@router.get("/{collection_id}/media", response_model=CollectionMediaResponse)
async def get_collection_media(
    auth: AuthDep,
    collection_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False, description="Force refresh cache"),
):
    """
    Get videos matching a collection's rules.

    Results are cached for 5 minutes unless refresh=true.
    """
    repo = get_collections_repository()
    service = CollectionsService()

    collection = await repo.get_collection_by_id(collection_id, auth.user_id)
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    media, total = await service.get_collection_media(
        collection_id=collection_id,
        user_id=auth.user_id,
        page=page,
        page_size=page_size,
        use_cache=not refresh,
    )

    return CollectionMediaResponse(
        collection_id=collection["id"],
        collection_name=collection["name"],
        media=media,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{collection_id}/refresh", response_model=CollectionRefreshResult)
async def refresh_collection(
    auth: AuthDep,
    collection_id: str,
):
    """Force refresh a collection's cache."""
    repo = get_collections_repository()
    service = CollectionsService()

    collection = await repo.get_collection_by_id(collection_id, auth.user_id)
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    count = await service.refresh_collection_cache(collection_id, auth.user_id)

    return {
        "message": "Collection refreshed",
        "collection_id": collection_id,
        "media_count": count,
    }


@router.post(
    "/init-presets",
    response_model=CollectionInitPresetsResult,
    response_model_exclude_unset=True,
)
async def initialize_preset_collections(auth: AuthDep):
    """
    Initialize preset collections for the current user.

    Only creates presets if the user doesn't have any yet.
    """
    repo = get_collections_repository()

    existing_presets = await repo.get_preset_collections(auth.user_id)
    if existing_presets:
        return {
            "message": "Preset collections already exist",
            "count": len(existing_presets),
        }

    created = await repo.create_default_presets(auth.user_id)

    return {
        "message": "Preset collections created",
        "count": len(created),
        "presets": [{"id": c["id"], "name": c["name"]} for c in created],
    }
