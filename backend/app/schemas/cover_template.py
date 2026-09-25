"""Pydantic models for the cover-template library (migration 441).

The library is a system folder; these are the wire shapes for reading it.
Snowflake ids cross the wire as STRINGS (they exceed 2^53). Every route wraps
its payload as ``{"data": ...}`` (no ``success``), i.e. ``DataEnvelope``;
wire parity is pinned by ``tests/api/test_cover_templates_wire.py``.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.wire import WireDatetime

# The reference pool is capped at nine server-side (canvas_generation.py and
# codex_cli.py, independently). A usage call carrying more than nine means the
# caller is confused; refuse rather than build a huge IN clause.
MAX_TEMPLATES_PER_RUN = 9


class CoverTemplateFolderOut(BaseModel):
    folder_id: str
    name: str
    # True on the one response that claimed a folder the user had already made,
    # so the UI can tell them "your 封面 folder is now the template library".
    adopted: bool = False


class CoverTemplateOut(BaseModel):
    resource_id: str
    name: str
    mime_type: Optional[str] = None
    # Unauthenticated thumbnail route, safe in a bare <img src>. NOT a
    # generation reference URL — that is minted on selection via
    # /generated-media/import-from-resource.
    thumb_url: str
    usage_count: int
    # Native datetime from the usage join: ``isoformat()`` on the wire
    # (``+00:00``), exactly what the bare dict sent. NULL = never used.
    last_used_at: Optional[WireDatetime] = None


class CoverTemplateListOut(BaseModel):
    folder: CoverTemplateFolderOut
    items: list[CoverTemplateOut]
    # Paging — the studio's picker searches and loads more, never lists all.
    total: int = 0
    limit: int = 48
    offset: int = 0


class CoverTemplateUseOut(BaseModel):
    """``POST /cover-templates/use``: how many ids were counted."""

    counted: int


class CoverTemplateUseRequest(BaseModel):
    resource_ids: list[str] = Field(
        default_factory=list, max_length=MAX_TEMPLATES_PER_RUN
    )
