"""The API's model-type vocabulary must match the one the database accepts.

THE DRIFT
─────────
Migration 345 widened the table's CHECK constraint to

    type = ANY (ARRAY['llm','embedding','tts','asr','image','video'])

and every image/video catalog row since (jimeng-cli-image, codex-image,
openai-image-flare, …) was inserted by a migration, straight into the table.
``MediahubModelType`` — the Literal both the create and update bodies validate
against — was never widened with it and still stops at 'asr'.

So the API rejects two types the database stores. Nothing surfaced it, because
the rows get created by SQL and read by a response model that types ``type`` as
a plain ``str``. Only the WRITE path is narrow, and only for the types no test
ever wrote.

The user-visible symptom, on production as of 2026-09-21: seven of seventeen
catalog rows are image or video, and the admin Edit dialog always sends ``type``
back. Editing any one of them — to fix a display name, a price, anything —
fails 422 before it reaches the database.

Same family as the slot-table mirror: one vocabulary, two places, no guard.
``test_mediahub_model_type_matches_db_check`` (tests/db/) is the guard.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.mediahub_model import (
    MediahubModelCreate,
    MediahubModelType,
    MediahubModelUpdate,
)

# The DB CHECK from migration 345, verbatim.
_DB_TYPES = ("llm", "embedding", "tts", "asr", "image", "video")


@pytest.mark.unit
def test_literal_covers_every_type_the_db_accepts():
    assert set(get_args(MediahubModelType)) == set(_DB_TYPES)


@pytest.mark.unit
@pytest.mark.parametrize("model_type", _DB_TYPES)
def test_update_accepts_every_db_type(model_type: str):
    """The exact failure an admin hits: the Edit dialog echoes the row's own
    ``type`` back in the PATCH body, so a narrow Literal makes image and video
    rows uneditable — not just untypeable."""
    assert MediahubModelUpdate(type=model_type).type == model_type


@pytest.mark.unit
@pytest.mark.parametrize("model_type", _DB_TYPES)
def test_create_accepts_every_db_type(model_type: str):
    body = MediahubModelCreate(
        name="t",
        display_name="T",
        type=model_type,
        actual_provider="codex",
        actual_model="m",
    )
    assert body.type == model_type


@pytest.mark.unit
def test_a_type_the_db_would_reject_is_still_refused():
    """The negative control. Widening a Literal until everything passes is not
    a fix — the CHECK constraint would then be the only thing catching a typo,
    and it catches it as a 500 rather than a 422."""
    with pytest.raises(ValidationError):
        MediahubModelUpdate(type="not-a-real-type")
