"""Wire models for the产出血缘 endpoints (三期 3a spec §4).

Three shapes, one rule: **every id leaves as a string**. ``run_deliverables``
ids, ``run_id`` and ``issue_id`` are Snowflake BIGINTs, and PostgREST-style
JSON numbers above 2^53 lose precision the moment the browser parses them
(CLAUDE.md「Snowflake BIGINT 精度丢失」). The repository already stringifies
them; these models make that part of the contract instead of an accident of
one query.

``cost_cents`` is a float and may be ``None`` — no generated-media call site
fills it today, and the UI shows ``—`` rather than a fabricated 0.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OutputVersion(BaseModel):
    """One row of ``run_deliverables``, decorated with the issue its run
    belonged to. ``issue_id`` is ``None`` for a run that answers to no issue
    (a canvas or chat lane run)."""

    id: str
    version: int
    parent_version: Optional[int] = None
    run_id: str
    issue_id: Optional[str] = None
    seq: Optional[int] = None
    turn: Optional[int] = None
    step: Optional[int] = None
    title: Optional[str] = None
    model: Optional[str] = None
    cost_cents: Optional[float] = None
    created_at: Optional[str] = None


class OutputObject(BaseModel):
    """Every version of ONE object. The panel's unit is the object, not the
    row: three revisions of one shot are one entry with three versions."""

    kind: str
    ref_id: str
    title: Optional[str] = None
    latest_version: int
    versions: List[OutputVersion]


class IssueOutputsResponse(BaseModel):
    items: List[OutputObject]


class OutputLineageResponse(BaseModel):
    kind: str
    ref_id: str
    latest_version: int
    versions: List[OutputVersion]


class OutputDiffMedia(BaseModel):
    """The媒体类 side of a diff: the row plus the two same-origin URLs the UI
    already knows how to render."""

    id: str
    media_kind: Optional[str] = None
    mime: Optional[str] = None
    cover_url: Optional[str] = None
    stream_url: Optional[str] = None


class OutputDiffSide(BaseModel):
    """One version, as content.

    ``available`` is its own field rather than "text is None": a version whose
    snapshot cannot be reconstructed and a version whose text is genuinely
    empty are different facts, and ``unavailable_reason`` says which one this
    is instead of leaving the panel to guess.
    """

    version: int
    run_id: str
    issue_id: Optional[str] = None
    created_at: Optional[str] = None
    model: Optional[str] = None
    cost_cents: Optional[float] = None
    title: Optional[str] = None
    text: Optional[str] = None
    media: Optional[OutputDiffMedia] = None
    available: bool = True
    unavailable_reason: Optional[str] = None


class OutputDiffResponse(BaseModel):
    """``from`` is a Python keyword, so the field is ``from_`` with an alias.
    FastAPI serialises response models by alias, so the wire key is ``from``."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str
    ref_id: str
    content_type: Literal["text", "media"]
    from_: OutputDiffSide = Field(alias="from")
    to: OutputDiffSide


__all__ = [
    "IssueOutputsResponse",
    "OutputDiffMedia",
    "OutputDiffResponse",
    "OutputDiffSide",
    "OutputLineageResponse",
    "OutputObject",
    "OutputVersion",
]
