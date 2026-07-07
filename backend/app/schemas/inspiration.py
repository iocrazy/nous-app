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
