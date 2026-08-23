"""Pydantic models for the cover-template library (migration 435).

⚠️ Snowflake ids cross the wire as **strings**, never numbers. They exceed
2^53, so a JSON number loses precision in the browser silently. This router
therefore matches the ``canvases`` convention (explicit ``str(...)``), not the
``scenes``/``shots`` one that returns raw ORM dicts as JSON numbers — the two
genuinely differ per resource and the difference is deliberate, so any test
faking this endpoint must fake **strings**.
"""

from __future__ import annotations

import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 'upload'    — the user handed us a file
# 'library'   — picked out of the resource library (source_resource_id is set)
# 'generated' — kept from a finished cover via "Save as template"
CoverTemplateSource = Literal["upload", "library", "generated"]


# The 9-image reference pool is capped server-side in two independent places
# (canvas_generation.py and codex_cli.py). A "these were used" call carrying
# more ids than can physically be sent to the model means the caller is
# confused, so it is rejected rather than turned into a 100k-element IN clause.
MAX_TEMPLATES_PER_RUN = 9


def _require_named(value: str) -> str:
    """Trim, then insist something is left.

    ``min_length=1`` inspects the RAW string, so a name of four spaces passes
    validation and becomes "" the moment anyone strips it — an unnamed card in
    the template grid that the user cannot select by name or explain. Trimming
    inside the validator makes the stored value and the validated value the
    same string, which the call sites no longer have to remember to do.
    """
    trimmed = (value or "").strip()
    if not trimmed:
        raise ValueError("name cannot be blank")
    return trimmed


class CoverTemplateOut(BaseModel):
    id: str
    name: str
    generated_media_id: str
    # Ready to drop straight into an <img src> and into a generation's
    # params.source_urls — those are the same URL on purpose, and the reason
    # the row anchors to generated_media at all (see migration 435).
    image_url: str
    source_kind: CoverTemplateSource
    source_resource_id: Optional[str] = None
    usage_count: int
    last_used_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class CoverTemplateListOut(BaseModel):
    items: list[CoverTemplateOut]


class CoverTemplateCreateFromMedia(BaseModel):
    """Turn an existing generated-media row into a template.

    Both UI paths funnel here: "upload" first POSTs the file to
    /generated-media/import, "from library" first POSTs the resource id to
    /generated-media/import-from-resource. Both hand back a generated_media
    id, which is what this endpoint takes — so neither path re-implements
    storage, and neither can accidentally produce a URL the generation bridge
    cannot read.
    """

    generated_media_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=120)
    source_kind: CoverTemplateSource = "upload"
    source_resource_id: Optional[str] = None

    _strip_name = field_validator("name")(lambda cls, v: _require_named(v))


class CoverTemplateRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)

    _strip_name = field_validator("name")(lambda cls, v: _require_named(v))


class CoverTemplateUseRequest(BaseModel):
    """Record that these templates were just handed to the model.

    Ids rather than a single id: one generation can cite several templates,
    and counting them in one call keeps "used N×" consistent with "how many
    times did I press Generate with this template in the pool".
    """

    template_ids: list[str] = Field(
        default_factory=list, max_length=MAX_TEMPLATES_PER_RUN
    )
