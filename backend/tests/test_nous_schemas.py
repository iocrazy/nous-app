# backend/tests/test_nous_schemas.py
"""nous schemas use the type enum (not category) + carry description."""

import pytest
from pydantic import ValidationError

from app.schemas.nous import (
    NousModelCreate,
    NousModelPublic,
    NousModelResponse,
    NousModelUpdate,
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
    m = NousModelCreate(**_create_kwargs(description="fast, cheap"))
    assert m.type == "llm"
    assert m.description == "fast, cheap"


def test_create_rejects_legacy_category_value():
    with pytest.raises(ValidationError):
        NousModelCreate(**_create_kwargs(type="transcription"))


def test_update_type_optional_and_description():
    m = NousModelUpdate(type="asr", description="short clips")
    assert m.type == "asr"
    assert m.description == "short clips"


def test_public_and_response_expose_type_and_description():
    assert "type" in NousModelPublic.model_fields
    assert "description" in NousModelPublic.model_fields
    assert "type" in NousModelResponse.model_fields
    assert "description" in NousModelResponse.model_fields
    assert "category" not in NousModelPublic.model_fields
