"""Pydantic models for the cover-template library (migration 441).

The library is a system folder; these are the wire shapes for reading it.
Snowflake ids cross the wire as STRINGS (they exceed 2^53).
"""

from __future__ import annotations

import datetime
from typing import Optional

from pydantic import BaseModel, Field

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
    last_used_at: Optional[datetime.datetime] = None


class CoverTemplateListOut(BaseModel):
    folder: CoverTemplateFolderOut
    items: list[CoverTemplateOut]


class CoverTemplateUseRequest(BaseModel):
    resource_ids: list[str] = Field(
        default_factory=list, max_length=MAX_TEMPLATES_PER_RUN
    )
