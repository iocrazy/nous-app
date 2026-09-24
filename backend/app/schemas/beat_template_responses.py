"""Response shapes of ``/api/v1/beat-templates`` (``app/api/beat_templates_router.py``).

Every model mirrors the dict the repository already builds, key for key
(spec 2026-09-24-openapi-typed-frontend-design.md §5; wire tests in
``tests/api/test_beat_templates_wire.py``). ``_row`` in
``beat_template_repository`` turns UUIDs and datetimes into strings and keeps
the Snowflake ``id`` a JSON number.

``anchors`` is JSONB. Every anchor the API ever wrote came from
``BeatTemplateAnchor.model_dump()`` and so carries all five keys, but the
column itself promises nothing. The anchor model therefore requires none of
them and keeps unknown keys; the routes are declared
``response_model_exclude_unset`` so a key a row lacks stays absent instead of
turning into ``null`` (or into a 500).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.envelope import Envelope


class BeatTemplateAnchorRow(BaseModel):
    """One stored anchor. ``pctStart`` / ``pctEnd`` are percentages 0-100."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    summary: str | None = None
    pctStart: int | float | None = None
    pctEnd: int | float | None = None
    color: str | None = None


class BeatTemplateRow(BaseModel):
    """One ``beat_templates`` row."""

    id: int
    user_id: str
    name: str
    anchors: list[BeatTemplateAnchorRow]
    created_at: str
    updated_at: str


class BeatTemplateListResponse(Envelope[list[BeatTemplateRow]]):
    pass


class BeatTemplateResponse(Envelope[BeatTemplateRow]):
    pass


class BeatTemplateAck(BaseModel):
    """``DELETE /beat-templates/{id}``: no ``data`` key."""

    success: bool
