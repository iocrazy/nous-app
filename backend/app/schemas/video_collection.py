"""Video Collection schemas for API requests and responses."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class VideoCollectionCreate(BaseModel):
    """Request to create a video collection."""
    name: str
    team_id: Optional[str] = None


class VideoCollectionUpdate(BaseModel):
    """Request to update a video collection."""
    name: Optional[str] = None


class VideoCollectionResponse(BaseModel):
    """Video collection response."""
    id: str
    name: str
    owner_id: str
    team_id: Optional[str] = None
    created_at: datetime
    video_count: int = 0
    is_shared: bool = False
    thumbnail_url: Optional[str] = None


class VideoCollectionListResponse(BaseModel):
    """List of video collections response."""
    collections: List[VideoCollectionResponse]
    total: int


class AddVideoRequest(BaseModel):
    """Request to add a video to collection."""
    video_aweme_id: str


class VideoCollectionVideosResponse(BaseModel):
    """List of collection IDs a video belongs to."""
    collection_ids: List[str]


class CollectionVideosAwemeIdsResponse(BaseModel):
    """List of video aweme_ids in a collection."""
    aweme_ids: List[str]


class CollectionVideosAwemeIdsRequest(BaseModel):
    """Request to get video aweme_ids from multiple collections."""
    collection_ids: List[str]
