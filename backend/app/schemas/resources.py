# app/schemas/resources.py

"""
Resource library validation schemas.

Pydantic models for resource upload, CRUD, version management,
folder operations, tag binding, and smart folder rules.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

# ─── Resources ────────────────────────────────────────────


class ResourceUpdate(BaseModel):
    """Request body for updating resource metadata."""

    filename: Optional[str] = Field(None, min_length=1, max_length=500)
    notes: Optional[str] = Field(None, max_length=5000)
    gen_prompt: Optional[str] = Field(
        None,
        max_length=20000,
        description="AI generation prompt attached to this asset",
    )
    gen_prompt_zh: Optional[str] = Field(
        None,
        max_length=20000,
        description="Chinese-language AI generation prompt for this asset",
    )
    url: Optional[str] = Field(None, max_length=2000)
    rating: Optional[int] = Field(None, ge=0, le=5)
    is_trashed: Optional[bool] = None
    # Per-user download status
    video_download_status: Optional[str] = Field(
        None, description="User video download status"
    )
    music_download_status: Optional[str] = Field(
        None, description="User music download status"
    )
    cover_download_status: Optional[str] = Field(
        None, description="User cover download status"
    )
    image_download_status: Optional[str] = Field(
        None, description="User image download status"
    )


class GenPromptTranslateRequest(BaseModel):
    """Body for POST /resources/{id}/gen-prompt/translate."""

    target_lang: str = Field(
        ...,
        pattern="^(en|zh)$",
        description="Language to translate the prompt into",
    )


class BatchAssetAiRequest(BaseModel):
    """Body for POST /resources/ai/batch (batch caption / classify)."""

    resource_ids: List[str] = Field(..., min_length=1, max_length=50)
    operation: str = Field(
        ...,
        pattern="^(caption|classify)$",
        description="Which asset-AI workflow to dispatch per resource",
    )


class ChorusUpdate(BaseModel):
    """Body for PUT /resources/{id}/chorus. None clears the marker."""

    chorus_start_ms: Optional[int] = Field(None, ge=0)


class ResourceMoveRequest(BaseModel):
    """Request body for moving a resource to a folder."""

    folder_id: Optional[str] = Field(
        None, description="Target folder ID, null for root"
    )
    # PR-E Phase 3: scope_type dropped. scope_id (a globally-unique teams.id
    # snowflake) alone locates the scope; the value is derived from team.kind
    # where an INSERT still needs it. Pydantic ignores any leftover field a
    # stale client sends.
    scope_id: str = Field(..., description="Team ID (teams.id snowflake)")


# ─── Folders ──────────────────────────────────────────────


class FolderCreate(BaseModel):
    """Request body for creating a folder."""

    name: str = Field(..., min_length=1, max_length=200)
    parent_id: Optional[str] = None
    # PR-E Phase 3: scope_type dropped; derived from team.kind on INSERT.
    scope_id: str = Field(..., description="Team ID (teams.id snowflake)")
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)


class FolderUpdate(BaseModel):
    """Request body for updating a folder."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: Optional[int] = None
    is_trashed: Optional[bool] = None


# ─── Tags ─────────────────────────────────────────────────


class ResourceTagRequest(BaseModel):
    """Request body for tagging a resource."""

    tag_id: str = Field(..., description="Tag ID to associate")


# ─── Smart Folders ───────────────────────────────────────


class SmartFolderCondition(BaseModel):
    """A single rule condition for a smart folder."""

    field: str = Field(
        ...,
        description=(
            "Field to match: filename, file_type, tags, file_size_bytes, "
            "created_at, duration_seconds, resolution, source_type, mime_type"
        ),
    )
    op: str = Field(
        ...,
        description=(
            "Operator: eq, contains, starts_with, gt, lt, gte, lte, in, not_contains"
        ),
    )
    value: str = Field(..., description="Value to match against")


class SmartFolderRules(BaseModel):
    """Rule set for a smart folder (Eagle-style)."""

    operator: str = Field(
        "AND", pattern="^(AND|OR)$", description="Logical operator: AND or OR"
    )
    match: bool = Field(True, description="True to match, False to exclude")
    conditions: List[SmartFolderCondition]


class SmartFolderCreate(BaseModel):
    """Request body for creating a smart folder."""

    name: str = Field(..., min_length=1, max_length=200)
    # PR-E Phase 3: scope_type dropped; derived from team.kind on INSERT.
    scope_id: str = Field(..., description="Team ID (teams.id snowflake)")
    rules: SmartFolderRules
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)


class SmartFolderUpdate(BaseModel):
    """Request body for updating a smart folder."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    rules: Optional[SmartFolderRules] = None
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
