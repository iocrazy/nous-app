# backend/tests/test_nous_schemas.py
"""nous schemas use the type enum (not category) + carry description."""

import pytest
from pydantic import ValidationError

from app.schemas.mediahub_model import (
    MediahubModelCreate,
    MediahubModelPublic,
    MediahubModelResponse,
    MediahubModelUpdate,
)


def _create_kwargs(**over):
    base = dict(
        name="nous-llm",
        display_name="Nous LLM",
        type="llm",
        actual_provider="doubao",
        actual_model="doubao-pro-32k",
        api_key="sk-x",
    )
    base.update(over)
    return base


def test_create_accepts_type_enum_and_description():
    m = MediahubModelCreate(**_create_kwargs(description="fast, cheap"))
    assert m.type == "llm"
    assert m.description == "fast, cheap"


def test_create_rejects_legacy_category_value():
    with pytest.raises(ValidationError):
        MediahubModelCreate(**_create_kwargs(type="transcription"))


def test_update_type_optional_and_description():
    m = MediahubModelUpdate(type="asr", description="short clips")
    assert m.type == "asr"
    assert m.description == "short clips"


def test_public_and_response_expose_type_and_description():
    assert "type" in MediahubModelPublic.model_fields
    assert "description" in MediahubModelPublic.model_fields
    assert "type" in MediahubModelResponse.model_fields
    assert "description" in MediahubModelResponse.model_fields
    assert "category" not in MediahubModelPublic.model_fields
