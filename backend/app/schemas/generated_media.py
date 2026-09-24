"""Response shapes of ``/api/v1/generated-media`` (the Tier-1 generations store).

These declare what the router already sent; nothing here changes the wire.
Snowflake ids are strings on this surface because the repository's
``_normalize`` stringifies them (``_BIGINT_COLS``) — unlike ``/projects``,
where they stay JSON numbers. ``created_at`` is the native ``datetime`` the
ORM returns, so it is :data:`WireDatetime` (``isoformat()``, not ``…Z``).

The Generated inbox (``/api/v1/generated``) projects the same table into a
different, derived shape: see :mod:`app.schemas.generated`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.wire import WireDatetime


class GeneratedMediaRow(BaseModel):
    """One ``generated_media`` row as the repository projects it (``_GM_COLS``).

    ``content_sha256`` is deliberately absent: the projection keeps it
    internal. ``tests/api/test_generated_media_wire.py`` pins this field set
    to the projection, so a column added there fails until it is declared.
    """

    id: str
    scope_id: str
    creator_id: str
    media_kind: str
    mime: Optional[str]
    file_path: str
    file_size_bytes: Optional[int]
    origin_kind: str
    origin_run_id: Optional[str]
    agent_id: Optional[str]
    canvas_id: Optional[str]
    node_id: Optional[str]
    prompt: Optional[str]
    model: Optional[str]
    provider: Optional[str]
    params: Dict[str, Any]
    cost_cents: Optional[float]
    parent_resource_id: Optional[str]
    derivation_kind: Optional[str]
    promoted_resource_id: Optional[str]
    review_state: str
    source_asset_id: Optional[str]
    conversation_id: Optional[str]
    created_at: WireDatetime


class GeneratedMediaPage(BaseModel):
    """A keyset page; ``next_cursor`` is null on the last page."""

    items: List[GeneratedMediaRow] = Field(default_factory=list)
    next_cursor: Optional[str] = None


class GeneratedMediaImported(BaseModel):
    """A file minted into the store, with the URL the canvas should read.

    ``url`` ends in ``/cover`` for images, ``/stream`` for videos and
    ``/file`` otherwise.
    """

    id: str
    url: str
    media_kind: str
    mime: str


class GeneratedMediaDeleted(BaseModel):
    """``deleted`` is false when no row matched in the caller's scope."""

    deleted: bool


class GeneratedMediaUpscaled(BaseModel):
    """The NEW row holding the upscaled image; the source row is untouched."""

    id: str
    url: str


class GeneratedMediaPromoted(BaseModel):
    promoted_resource_id: str
