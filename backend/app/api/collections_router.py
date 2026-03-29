"""API routes for Smart Collections."""

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import AuthDep
from app.repositories.collections_repository import CollectionsRepository
from app.schemas.collections import (
    CollectionCreate,
    CollectionListResponse,
    CollectionResponse,
    CollectionRules,
    CollectionUpdate,
    CollectionMediaResponse,
)
from app.services.collections_service import CollectionsService

router = APIRouter(prefix="/collections", tags=["Collections"])


@router.get("", response_model=CollectionListResponse)
async def list_collections(
    auth: AuthDep,
    include_presets: bool = Query(True, description="Include preset collections"),
):
    """
    List all smart collections for the current user.

    Includes both user-created and preset collections by default.
    """
    repo = CollectionsRepository()
    collections = await repo.get_all_collections(auth.user_id)

    if not include_presets:
        collections = [c for c in collections if not c.get("is_preset")]

    return CollectionListResponse(
        collections=[
            CollectionResponse(
                id=c["id"],
                user_id=c["user_id"],
                name=c["name"],
                icon=c.get("icon", "📁"),
                color=c.get("color"),
                description=c.get("description"),
                rules=CollectionRules(
                    **c.get("rules", {"match": "all", "conditions": []})
                ),
                media_count=c.get("cached_count", 0),
                cached_at=c.get("cached_at"),
                is_preset=c.get("is_preset", False),
                is_active=c.get("is_active", True),
                sort_by=c.get("sort_by", "created_at"),
                sort_order=c.get("sort_order", "desc"),
                created_at=c["created_at"],
                updated_at=c.get("updated_at", c["created_at"]),
            )
            for c in collections
        ],
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
    repo = CollectionsRepository()

    created = await repo.create_collection(
        user_id=auth.user_id,
        name=collection.name,
        icon=collection.icon,
        description=collection.description,
        rules=collection.rules.model_dump(),
        sort_by=collection.sort_by,
        sort_order=collection.sort_order,
    )

    return CollectionResponse(
        id=created["id"],
        user_id=created["user_id"],
        name=created["name"],
        icon=created.get("icon", "📁"),
        color=created.get("color"),
        description=created.get("description"),
        rules=CollectionRules(
            **created.get("rules", {"match": "all", "conditions": []})
        ),
        media_count=created.get("cached_count", 0),
        cached_at=created.get("cached_at"),
        is_preset=created.get("is_preset", False),
        is_active=created.get("is_active", True),
        sort_by=created.get("sort_by", "created_at"),
        sort_order=created.get("sort_order", "desc"),
        created_at=created["created_at"],
        updated_at=created.get("updated_at", created["created_at"]),
    )


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection(
    auth: AuthDep,
    collection_id: str,
):
    """Get a specific collection by ID."""
    repo = CollectionsRepository()
    collection = await repo.get_collection_by_id(collection_id, auth.user_id)

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    return CollectionResponse(
        id=collection["id"],
        user_id=collection["user_id"],
        name=collection["name"],
        icon=collection.get("icon", "📁"),
        color=collection.get("color"),
        description=collection.get("description"),
        rules=CollectionRules(
            **collection.get("rules", {"match": "all", "conditions": []})
        ),
        media_count=collection.get("cached_count", 0),
        cached_at=collection.get("cached_at"),
        is_preset=collection.get("is_preset", False),
        is_active=collection.get("is_active", True),
        sort_by=collection.get("sort_by", "created_at"),
        sort_order=collection.get("sort_order", "desc"),
        created_at=collection["created_at"],
        updated_at=collection.get("updated_at", collection["created_at"]),
    )


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
    repo = CollectionsRepository()

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

    return CollectionResponse(
        id=updated["id"],
        user_id=updated["user_id"],
        name=updated["name"],
        icon=updated.get("icon", "📁"),
        color=updated.get("color"),
        description=updated.get("description"),
        rules=CollectionRules(
            **updated.get("rules", {"match": "all", "conditions": []})
        ),
        media_count=updated.get("cached_count", 0),
        cached_at=updated.get("cached_at"),
        is_preset=updated.get("is_preset", False),
        is_active=updated.get("is_active", True),
        sort_by=updated.get("sort_by", "created_at"),
        sort_order=updated.get("sort_order", "desc"),
        created_at=updated["created_at"],
        updated_at=updated.get("updated_at", updated["created_at"]),
    )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    auth: AuthDep,
    collection_id: str,
):
    """
    Delete a collection.

    Preset collections cannot be deleted.
    """
    repo = CollectionsRepository()

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
    repo = CollectionsRepository()
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


@router.post("/{collection_id}/refresh")
async def refresh_collection(
    auth: AuthDep,
    collection_id: str,
):
    """Force refresh a collection's cache."""
    repo = CollectionsRepository()
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


@router.post("/init-presets")
async def initialize_preset_collections(auth: AuthDep):
    """
    Initialize preset collections for the current user.

    Only creates presets if the user doesn't have any yet.
    """
    repo = CollectionsRepository()

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
