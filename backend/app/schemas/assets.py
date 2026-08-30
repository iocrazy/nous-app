"""Pydantic schemas for the asset library (mig 445/446).

Snowflake ids are strings at this boundary (bigIntSafeFetch discipline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# Snowflake ids ride as strings here (bigIntSafeFetch discipline), but the
# service int()s them. Without this the first non-numeric body value is a
# ValueError deep in the service — a 500 where the caller should have got a 422.
SNOWFLAKE_PATTERN = r"^[0-9]{1,20}$"

# ``assets.id`` and every id it references are PostgreSQL BIGINT. The pattern
# alone admits 20 digits, i.e. up to 10^20-1, which is ~10x past int64: the
# value parses, reaches the driver, and fails at BIND — a 500 handed out for
# hostile input. Bound it where the string is validated instead.
_INT64_EXCLUSIVE_MAX = 2**63


def within_int64(value: Optional[str]) -> Optional[str]:
    """Reject digit strings the BIGINT columns cannot hold (``>= 2**63``)."""
    if value is not None and int(value) >= _INT64_EXCLUSIVE_MAX:
        raise ValueError(f"id must be < {_INT64_EXCLUSIVE_MAX} (BIGINT range)")
    return value


SnowflakeId = Annotated[
    str, Field(pattern=SNOWFLAKE_PATTERN), AfterValidator(within_int64)
]

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
    """PATCH payload — **omit = unchanged, explicit null = clear**.

    The two are told apart by ``model_fields_set`` (the service dumps with
    ``exclude_unset=True``), not by the value: an omitted key never reaches the
    UPDATE, while ``{"cover_file_id": null}`` writes NULL. Before that, both
    read as ``None`` and "remove this asset's cover / subtype / prompt" was
    unreachable through the API — the request answered 200 and changed nothing.

    Only the nullable columns can be cleared —
    ``subtype / cover_file_id / prompt_positive / prompt_negative /
    prompt_positive_zh / prompt_negative_zh``. A null aimed at a NOT NULL
    column (``name``, ``attrs``, ``tags``, …) is a typed 422
    (``field_not_nullable``), never a dropped key.

    ``extra="forbid"``: without it ``{"promptpositive": "..."}`` answered 200
    with the asset untouched, so a typo'd field was indistinguishable from a
    successful edit — the silent-no-op class this module refuses everywhere else.
    """

    model_config = ConfigDict(extra="forbid")

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
    cover_file_id: Optional[SnowflakeId] = None
    tags: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class AssetReadiness(BaseModel):
    state: ReadinessState
    missing: List[str] = Field(default_factory=list)


class AssetResponse(BaseModel):
    id: str
    # NULL for global system presets (assets_scope_or_preset CHECK).
    scope_id: Optional[str] = None
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
    # Declared because ``_serialize_file`` emits it: once the routes carry a
    # response_model, any key missing from the model is silently dropped from
    # the wire — a removal no test or log would report.
    attached_by: Optional[str] = None
    attached_at: datetime


class AssetLinkResponse(BaseModel):
    from_asset_id: str
    to_asset_id: str
    relation: LinkRelation
    # Same reason as AssetFileResponse.attached_by — ``_serialize_link`` emits it.
    created_at: Optional[datetime] = None


class LoadoutCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    costume_ids: List[SnowflakeId] = Field(default_factory=list)
    prop_ids: List[SnowflakeId] = Field(default_factory=list)
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)


class LoadoutUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged".

    ``is_default=False`` is NOT an operation: ``uq_loadout_default`` means an
    asset has exactly one default, so there is no "unset the default", only
    "make a different one default". A False is therefore dropped, deliberately.

    ``extra="forbid"`` for the same reason as :class:`AssetUpdate`.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    costume_ids: Optional[List[SnowflakeId]] = None
    prop_ids: Optional[List[SnowflakeId]] = None
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
    resource_id: SnowflakeId
    slot: str = Field(default="unsorted", min_length=1)
    loadout_id: Optional[SnowflakeId] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class AttachFilesBatchRequest(BaseModel):
    items: List[AttachFileRequest] = Field(..., min_length=1, max_length=200)


class LinkRequest(BaseModel):
    to_asset_id: SnowflakeId
    relation: LinkRelation


class ProjectRefRequest(BaseModel):
    project_id: SnowflakeId


# ── response envelopes ─────────────────────────────────────────────────────
# Every /assets route answers ``{success, data}`` on the way out and
# ``{success:false, error:{code, detail, ...}}`` on refusal. Declaring both as
# models is what makes FastAPI VALIDATE the payload (a service that stops
# emitting ``readiness`` now fails loudly instead of shipping a half row) and
# what puts a real schema in the OpenAPI document the frontend types read off.

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Success wrapper. ``success`` is always True here — a failure is an
    :class:`ErrorEnvelope`, returned as a raw JSONResponse so it bypasses this
    model rather than being coerced into it."""

    success: bool = True
    data: T


class ErrorEnvelope(BaseModel):
    """Refusal wrapper. ``error`` stays an open dict on purpose: each
    ``AssetError`` carries ``code`` + ``detail`` plus its own extras
    (``existing_asset_id``, ``costume_ids``, …), and narrowing the model would
    drop exactly the field a client needs to act on."""

    success: bool = False
    error: Dict[str, Any]


class DeletedResponse(BaseModel):
    deleted: bool = True


class DetachedResponse(BaseModel):
    detached: bool = True


class RemovedResponse(BaseModel):
    removed: bool = True


class LinkedResponse(BaseModel):
    linked: bool = True


class UnlinkedResponse(BaseModel):
    unlinked: bool = True
