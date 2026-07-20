"""Beat memo request/response schemas (Beats M5).

A memo is a whole-second-anchored note that lives IN a script's Beats
arrangement timeline (a laper-style node), never in the inspiration library.
``images`` are object-store path strings the memo-image upload endpoint minted —
kept under the ``beats/memos/`` prefix and validated here so a direct API caller
cannot smuggle an arbitrary bucket path (path-traversal / cross-bucket read) into
the memo-image serve route.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# PG INTEGER ceiling — anchor_sec is an INTEGER column; asyncpg binds it strictly,
# so an out-of-range client value must 422 at the boundary, not blow up as 22003.
_PG_INT_MAX = 2_147_483_647

# Object-store keys the upload endpoint mints all live under this prefix. Echoing
# anything else back (or a ".." traversal) is rejected — a memo image can only
# ever point at a memo image.
_MEMO_IMAGE_PREFIX = "beats/memos/"


def _validate_image_paths(paths: List[str]) -> List[str]:
    for p in paths:
        if not isinstance(p, str) or not p.startswith(_MEMO_IMAGE_PREFIX) or ".." in p:
            raise ValueError("invalid memo image path")
    return paths


class MemoCreate(BaseModel):
    """Request body for `POST /scripts/{script_id}/memos`. ``script_id`` comes
    from the path; the anchor is required (a memo is always timeline-placed)."""

    anchor_sec: int = Field(..., ge=0, le=_PG_INT_MAX)
    content: str = Field("", max_length=5000)
    images: List[str] = Field(default_factory=list, max_length=4)

    @field_validator("images")
    @classmethod
    def _check_images(cls, v: List[str]) -> List[str]:
        return _validate_image_paths(v)


class MemoUpdate(BaseModel):
    """Request body for `PATCH /memos/{memo_id}` — true PATCH semantics: only the
    fields the client actually sent are forwarded (the router reads
    ``model_fields_set`` / ``exclude_unset``)."""

    anchor_sec: Optional[int] = Field(None, ge=0, le=_PG_INT_MAX)
    content: Optional[str] = Field(None, max_length=5000)
    images: Optional[List[str]] = Field(None, max_length=4)

    @field_validator("images")
    @classmethod
    def _check_images(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return v if v is None else _validate_image_paths(v)


class MemoOut(BaseModel):
    """API response for a single memo. Bigint id / script_id serialize to string
    (coerce_numbers_to_str) so JS never loses Snowflake precision; anchor_sec is a
    real INTEGER and stays an int."""

    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    script_id: str
    anchor_sec: int
    content: str
    images: List[str]
    created_at: str
    updated_at: str
