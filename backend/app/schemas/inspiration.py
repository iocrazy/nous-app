"""Pydantic schemas for the inspiration notes REST API (spec §4)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# PG INTEGER ceiling — asyncpg binds anchor_sec strictly, so a client value
# beyond this must be rejected at the boundary, not clipped by the DB.
_INT4_MAX = 2_147_483_647


class NoteCreateIn(BaseModel):
    content_md: str = Field(..., max_length=100_000)
    ref_hotspot: Optional[Dict[str, Any]] = None
    # Beats M4 timeline anchor (both together, or neither). anchor_script_id is a
    # Snowflake bigint carried as a string so JS never loses precision.
    anchor_script_id: Optional[str] = None
    anchor_sec: Optional[int] = Field(None, ge=0, le=_INT4_MAX)


class NoteUpdateIn(BaseModel):
    content_md: Optional[str] = Field(None, max_length=100_000)
    pinned: Optional[bool] = None
    # Explicit null clears the anchor (un-pin from the timeline); an absent field
    # leaves it untouched (true PATCH semantics via model_fields_set in the router).
    anchor_script_id: Optional[str] = None
    anchor_sec: Optional[int] = Field(None, ge=0, le=_INT4_MAX)


class AttachmentOut(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    mime: str
    size_bytes: int
    original_name: str


class NoteOut(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    content_md: str
    tags: List[str]
    ref_hotspot: Optional[Dict[str, Any]] = None
    pinned: bool
    note_date: str
    anchor_script_id: Optional[str] = None
    anchor_sec: Optional[int] = None
    created_at: str
    updated_at: str
    attachments: List[AttachmentOut] = []


# ─── Personal Access Tokens (spec §3.3 — external ingestion) ─────────────────


class ApiTokenCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class ApiTokenOut(BaseModel):
    """Token metadata — never carries the plaintext or its hash."""

    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    name: str
    last_used_at: Optional[str] = None
    created_at: str
    revoked_at: Optional[str] = None


class ApiTokenCreated(ApiTokenOut):
    """Create response — the plaintext `token` is returned exactly once."""

    token: str
