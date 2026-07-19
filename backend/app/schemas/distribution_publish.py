"""Distribution — publish task schemas (PR-D2).

Ported from the media-router prototype (models/schemas.py TaskCreate /
TaskResponse) and rewritten to mediahub conventions: BIGINT ids serialize as
str (JS 2^53), dual-channel ('official'|'h5'), visibility triad matching the
publish_tasks CHECK constraint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ContentType = Literal["video", "images", "article"]
Visibility = Literal["public", "friends", "private"]
DistributionMode = Literal["broadcast", "one_to_one"]
Channel = Literal["official", "h5"]

# Topics == Douyin hashtags (话题). The UI collects them as chips and the
# workflow delivers them to Douyin (H5 hashtag_list JsonArray / official post
# text `#tag `). Bound so a single batch can't carry an absurd hashtag wall.
MAX_TOPICS = 20
MAX_TOPIC_LEN = 50

# Douyin caps an image / gallery (图文/note) post at 35 images. The H5 share
# doc doesn't state the ceiling explicitly, so this mirrors the app's known
# gallery limit — bound so a single note can't carry an absurd image wall.
MAX_IMAGES = 35


def normalize_topics(topics: Optional[list[str]]) -> list[str]:
    """Sanitize user-entered topics into bare hashtag words: strip surrounding
    whitespace and leading '#' characters, drop empties (a stray '#' or a
    trailing comma must not 422 the whole publish), reject over-long tags, and
    cap the count. Returns a fresh list (never mutates the input)."""
    if not topics:
        return []
    out: list[str] = []
    for raw in topics:
        tag = raw.strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TOPIC_LEN:
            raise ValueError(f"topic exceeds {MAX_TOPIC_LEN} characters: {tag!r}")
        out.append(tag)
    if len(out) > MAX_TOPICS:
        raise ValueError(f"at most {MAX_TOPICS} topics allowed")
    return out


class AccountConfigOverride(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    topics: Optional[list[str]] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return None if v is None else normalize_topics(v)


class PublishTaskCreate(BaseModel):
    content_type: ContentType = "video"
    resource_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    visibility: Visibility = "public"
    ai_content: bool = False
    allow_download: bool = True
    distribution_mode: DistributionMode = "broadcast"
    channel: Channel = "h5"
    account_ids: list[str] = Field(min_length=1)
    account_configs: dict[str, AccountConfigOverride] = Field(default_factory=dict)
    cover_vertical_resource_id: Optional[str] = None
    cover_horizontal_resource_id: Optional[str] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: list[str]) -> list[str]:
        return normalize_topics(v)

    @model_validator(mode="after")
    def _validate_content(self) -> "PublishTaskCreate":
        if self.content_type in ("video", "images") and not self.resource_ids:
            raise ValueError("resource_ids required for video/images content")
        if self.content_type == "images" and len(self.resource_ids) > MAX_IMAGES:
            raise ValueError(f"at most {MAX_IMAGES} images allowed")
        # one_to_one round-robins a resource per account — that's a VIDEO
        # semantic. An images task is one note carrying every image (broadcast
        # to each account, never split), so the count check doesn't apply.
        if (
            self.content_type == "video"
            and self.distribution_mode == "one_to_one"
            and len(self.resource_ids) < len(self.account_ids)
        ):
            raise ValueError("one_to_one needs at least as many resources as accounts")
        return self


class TaskAccountOut(BaseModel):
    id: str
    account_id: str
    username: str
    avatar_url: Optional[str] = None
    channel: str
    status: str
    error_message: Optional[str] = None
    published_url: Optional[str] = None
    platform_item_id: Optional[str] = None
    published_at: Optional[datetime] = None


class PublishTaskOut(BaseModel):
    id: str
    content_type: str
    title: str
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    visibility: str = "public"
    distribution_mode: str = "broadcast"
    status: str  # aggregated display status (see aggregate_task_status)
    created_at: datetime
    accounts: list[TaskAccountOut] = Field(default_factory=list)


class PublishTaskListResponse(BaseModel):
    tasks: list[PublishTaskOut]


class ShareSchemaResponse(BaseModel):
    schema_url: str
    share_id: str
