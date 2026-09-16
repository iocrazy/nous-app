"""Request/response models for /api/v1/playback-positions (mig 473)."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

# Mirrors the CHECK on playback_positions.media_key. Validating here turns a
# runaway key into a 422 with a readable message instead of a 500 from the
# constraint.
MEDIA_KEY_MAX = 512

# One screen's worth of keys. Matches MAX_KEYS_PER_READ in the repository.
MAX_KEYS_PER_READ = 100


class PlaybackPositionWrite(BaseModel):
    """Body for PUT /playback-positions."""

    media_key: str = Field(min_length=1, max_length=MEDIA_KEY_MAX)
    position_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)


class PlaybackPosition(BaseModel):
    """One resume point."""

    media_key: str
    position_seconds: float
    duration_seconds: float
    # ISO-8601 SERVER time. The client stores this verbatim and compares it on
    # the next load to tell its own write from another device's.
    updated_at: str


class PlaybackPositionList(BaseModel):
    positions: List[PlaybackPosition]
