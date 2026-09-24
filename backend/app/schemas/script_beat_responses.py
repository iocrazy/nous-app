"""Response shapes of the Beats view routes (beats and timeline memos).

These declare what the routers already sent; nothing here changes the wire.

- **Beats** come out of ``script_beat_repository._row`` (strategy-C parity):
  bigint ``id`` / ``script_id`` stay JSON **numbers**, timestamps are already
  ISO strings (declared ``str``), ``scene_ids`` is a list of id strings (the
  repository string-coerces it on every write).
- **Memos** go through :class:`BeatMemoOut`, which turns the bigint ids into
  strings (``coerce_numbers_to_str``) — so on this surface ids are strings.

``tests/api/test_script_beats_wire.py`` pins each row model's field set to its
ORM model's columns, so a column added to the table fails until it is declared
here.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class ScriptBeatRow(BaseModel):
    """One ``script_beats`` row (``SELECT *`` shape)."""

    id: int
    script_id: int
    title: str
    summary: Optional[str]
    # Ordered linked scene ids (Snowflake strings).
    scene_ids: List[str]
    sort_order: int
    # Arrangement columns (mig 372); NULL start_sec = not yet arranged.
    start_sec: Optional[int]
    duration_sec: Optional[int]
    beat_role: Optional[str]
    color: Optional[str]
    created_at: str
    updated_at: str


class BeatMemoOut(BaseModel):
    """One ``beat_memos`` row as the API returns it. Bigint ``id`` /
    ``script_id`` serialize to strings (``coerce_numbers_to_str``) so JS never
    loses Snowflake precision; ``anchor_sec`` is a real INTEGER and stays an
    int. The handlers build their payload through this model, and it is also
    the declared response model."""

    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")

    id: str
    script_id: str
    anchor_sec: int
    content: str
    images: List[str]
    created_at: str
    updated_at: str


class BeatMemoImageUpload(BaseModel):
    """``POST /scripts/{id}/memos/upload``: the object-store path the caller
    then persists into the memo's ``images`` array."""

    path: str
