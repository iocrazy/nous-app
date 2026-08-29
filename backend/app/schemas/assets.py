"""Pydantic schemas for the asset library (mig 445/446).

Snowflake ids are strings at this boundary (bigIntSafeFetch discipline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

AssetType = Literal["character", "location", "prop", "costume", "prompt", "audio"]
AssetSource = Literal[
    "manual", "script_import", "generated", "migrated", "duplicated", "system_preset"
]
LinkRelation = Literal["wears", "holds", "ambience_of", "voice_of"]
ReadinessState = Literal["ready", "draft"]


class AssetCreate(BaseModel):
    asset_type: AssetType
    name: str = Field(..., min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=20000)
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    tags: Dict[str, Any] = Field(default_factory=dict)
    source: AssetSource = "manual"


class AssetUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged"."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=20000)
    attrs: Optional[Dict[str, Any]] = None
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Optional[Dict[str, Any]] = None
    cover_file_id: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class AssetReadiness(BaseModel):
    state: ReadinessState
    missing: List[str] = Field(default_factory=list)


class AssetResponse(BaseModel):
    id: str
    scope_id: str
    asset_type: AssetType
    subtype: Optional[str] = None
    name: str
    role_tag: str = ""
    description: str = ""
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = None
    prompt_negative: Optional[str] = None
    prompt_positive_zh: Optional[str] = None
    prompt_negative_zh: Optional[str] = None
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    cover_file_id: Optional[str] = None
    source: AssetSource = "manual"
    duplicated_from: Optional[str] = None
    is_system_preset: bool = False
    tags: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # derived
    readiness: AssetReadiness
    file_counts_by_slot: Dict[str, int] = Field(default_factory=dict)
    project_ids: List[str] = Field(default_factory=list)
    loadout_count: int = 0


class AssetFileResponse(BaseModel):
    asset_id: str
    resource_id: str
    slot: str
    loadout_id: Optional[str] = None
    sort_order: int = 0
    note: Optional[str] = None
    attached_at: datetime


class AssetLinkResponse(BaseModel):
    from_asset_id: str
    to_asset_id: str
    relation: LinkRelation


class LoadoutCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    costume_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)


class LoadoutUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    costume_ids: Optional[List[str]] = None
    prop_ids: Optional[List[str]] = None
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


class LoadoutResponse(BaseModel):
    id: str
    asset_id: str
    name: str
    is_default: bool
    costume_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    prompt_extra: Optional[str] = None
    sort_order: int = 0
    created_at: datetime


class AssetDetailResponse(AssetResponse):
    files: List[AssetFileResponse] = Field(default_factory=list)
    links: List[AssetLinkResponse] = Field(default_factory=list)  # outgoing
    linked_by: List[AssetLinkResponse] = Field(default_factory=list)  # incoming
    loadouts: List[LoadoutResponse] = Field(default_factory=list)


class AttachFileRequest(BaseModel):
    resource_id: str
    slot: str = "unsorted"
    loadout_id: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class AttachFilesBatchRequest(BaseModel):
    items: List[AttachFileRequest] = Field(..., min_length=1, max_length=200)


class LinkRequest(BaseModel):
    to_asset_id: str
    relation: LinkRelation


class ProjectRefRequest(BaseModel):
    project_id: str
