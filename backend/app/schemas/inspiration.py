"""Pydantic schemas for the inspiration notes REST API (spec §4)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NoteCreateIn(BaseModel):
    content_md: str = Field(..., max_length=100_000)
    ref_hotspot: Optional[Dict[str, Any]] = None


class NoteUpdateIn(BaseModel):
    content_md: Optional[str] = Field(None, max_length=100_000)
    pinned: Optional[bool] = None


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
