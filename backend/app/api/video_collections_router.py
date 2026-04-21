"""API routes for Video Collections (manual collections) management."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.video_collection_repository import VideoCollectionRepository
from app.schemas.video_collection import (
    AddVideoRequest,
    CollectionVideosAwemeIdsRequest,
    CollectionVideosAwemeIdsResponse,
    VideoCollectionCreate,
    VideoCollectionListResponse,
    VideoCollectionResponse,
    VideoCollectionUpdate,
    VideoCollectionVideosResponse,
)

router = APIRouter(prefix="/video-collections", tags=["Video Collections"])


@router.get("", response_model=VideoCollectionListResponse)
async def list_collections(auth: AuthDep):
    """List all video collections the user has access to."""
    repo = VideoCollectionRepository()
    collections = await repo.get_user_collections(auth.user_id)

    return VideoCollectionListResponse(
        collections=[
            VideoCollectionResponse(
                id=str(c["id"]),
                name=c["name"],
                owner_id=c["owner_id"],
                team_id=c.get("team_id"),
                created_at=c["created_at"],
                video_count=c.get("video_count", 0),
                is_shared=c.get("is_shared", False),
                thumbnail_url=c.get("thumbnail_url"),
            )
            for c in collections
        ],
        total=len(collections),
    )


@router.post(
    "", response_model=VideoCollectionResponse, status_code=status.HTTP_201_CREATED
)
async def create_collection(collection: VideoCollectionCreate, auth: AuthDep):
    """Create a new video collection."""
    repo = VideoCollectionRepository()

    try:
        created = await repo.create_collection(
            name=collection.name, owner_id=auth.user_id, team_id=collection.team_id
        )
    except Exception as e:
        logger.error(f"Failed to create collection: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create collection",
        )

    return VideoCollectionResponse(
        id=str(created["id"]),
        name=created["name"],
        owner_id=created["owner_id"],
        team_id=created.get("team_id"),
        created_at=created["created_at"],
        video_count=created.get("video_count", 0),
        is_shared=created.get("is_shared", False),
        thumbnail_url=created.get("thumbnail_url"),
    )


@router.get("/{collection_id}", response_model=VideoCollectionResponse)
async def get_collection(collection_id: str, auth: AuthDep):
    """Get a specific collection by ID."""
    repo = VideoCollectionRepository()
    collection = await repo.get_collection_by_id(collection_id, auth.user_id)

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found or access denied",
        )

    return VideoCollectionResponse(
        id=str(collection["id"]),
        name=collection["name"],
        owner_id=collection["owner_id"],
        team_id=collection.get("team_id"),
        created_at=collection["created_at"],
        video_count=collection.get("video_count", 0),
        is_shared=bool(collection.get("team_id")),
        thumbnail_url=collection.get("thumbnail_url"),
    )


@router.put("/{collection_id}", response_model=VideoCollectionResponse)
async def update_collection(
    collection_id: str, update: VideoCollectionUpdate, auth: AuthDep
):
    """Update a collection."""
    repo = VideoCollectionRepository()

    update_data = {}
    if update.name is not None:
        update_data["name"] = update.name

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No update data provided"
        )

    updated = await repo.update_collection(collection_id, auth.user_id, **update_data)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found or access denied",
        )

    return VideoCollectionResponse(
        id=str(updated["id"]),
        name=updated["name"],
        owner_id=updated["owner_id"],
        team_id=updated.get("team_id"),
        created_at=updated["created_at"],
        video_count=updated.get("video_count", 0),
        is_shared=bool(updated.get("team_id")),
        thumbnail_url=updated.get("thumbnail_url"),
    )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(collection_id: str, auth: AuthDep):
    """Delete a collection."""
    repo = VideoCollectionRepository()
    deleted = await repo.delete_collection(collection_id, auth.user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found or access denied",
        )


@router.post("/{collection_id}/videos", status_code=status.HTTP_201_CREATED)
async def add_video(collection_id: str, request: AddVideoRequest, auth: AuthDep):
    """Add a video to a collection."""
    repo = VideoCollectionRepository()

    try:
        added = await repo.add_video_to_collection(
            collection_id, request.video_aweme_id, auth.user_id
        )
    except Exception as e:
        if "Video not found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Video not found"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add video",
        )

    if not added:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found or access denied",
        )

    return {"message": "Video added to collection"}


@router.delete(
    "/{collection_id}/videos/{video_aweme_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_video(collection_id: str, video_aweme_id: str, auth: AuthDep):
    """Remove a video from a collection."""
    repo = VideoCollectionRepository()

    try:
        removed = await repo.remove_video_from_collection(
            collection_id, video_aweme_id, auth.user_id
        )
    except Exception as e:
        if "Video not found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Video not found"
            )
        raise

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found or access denied",
        )


@router.get("/video/{video_aweme_id}", response_model=VideoCollectionVideosResponse)
async def get_video_collections(video_aweme_id: str, auth: AuthDep):
    """Get all collection IDs a video belongs to."""
    repo = VideoCollectionRepository()
    collection_ids = await repo.get_video_collections(video_aweme_id, auth.user_id)

    return VideoCollectionVideosResponse(collection_ids=collection_ids)


@router.get("/{collection_id}/videos", response_model=CollectionVideosAwemeIdsResponse)
async def get_collection_videos(collection_id: str, auth: AuthDep):
    """Get all video aweme_ids in a collection."""
    repo = VideoCollectionRepository()
    aweme_ids = await repo.get_collection_video_aweme_ids(collection_id, auth.user_id)

    return CollectionVideosAwemeIdsResponse(aweme_ids=aweme_ids)


@router.post("/batch-videos", response_model=CollectionVideosAwemeIdsResponse)
async def get_batch_collection_videos(
    request: CollectionVideosAwemeIdsRequest, auth: AuthDep
):
    """Get all unique video aweme_ids from multiple collections."""
    repo = VideoCollectionRepository()
    aweme_ids = await repo.get_multiple_collections_video_aweme_ids(
        request.collection_ids, auth.user_id
    )

    return CollectionVideosAwemeIdsResponse(aweme_ids=aweme_ids)
