"""Wire shape of the unified prompt catalog (spec 2026-09-05 §3.1).

ONE shape for three storage places. Both surfaces (resource library Prompts
page, canvas Library panel) read this and nothing else; neither knows which
table a row came from beyond ``source.store``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PromptForm = Literal["template", "image", "album"]
PromptOrigin = Literal["typed", "extracted", "captioned"]
PromptSegment = Literal["mine", "project", "system"]


class PromptThumb(BaseModel):
    url: str
    kind: Literal["image"] = "image"


class PromptSlide(BaseModel):
    name: str
    #: None when the album's parsed_media row is gone — the text still shows.
    url: Optional[str] = None
    positive_en: Optional[str] = None
    positive_zh: Optional[str] = None
    negative_en: Optional[str] = None
    negative_zh: Optional[str] = None


class PromptSource(BaseModel):
    store: Literal["assets", "uploads"]
    id: str


class PromptEntry(BaseModel):
    key: str
    form: PromptForm
    #: None only for image/album rows the backfill has not labelled yet.
    origin: Optional[PromptOrigin] = None
    title: str
    tags: List[str] = Field(default_factory=list)
    positive_en: Optional[str] = None
    positive_zh: Optional[str] = None
    negative_en: Optional[str] = None
    negative_zh: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    thumbs: List[PromptThumb] = Field(default_factory=list)
    slides: Optional[List[PromptSlide]] = None
    source: PromptSource
    updated_at: str


class PromptPage(BaseModel):
    items: List[PromptEntry]
    total: int
    by_form: Dict[str, int]
    by_origin: Dict[str, int]


class PromptCounts(BaseModel):
    mine: int
    #: Only when the request named a project.
    project: Optional[int] = None
    system: int
