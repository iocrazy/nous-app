# backend/tests/test_mediahub_model_orm.py
"""MediahubModels ORM model reflects migration 302 (type column + description)."""

from app.models import MediahubModels


def test_mediahub_models_has_type_column_not_category():
    attrs = {p.key for p in MediahubModels.__mapper__.column_attrs}
    assert "type" in attrs
    assert "category" not in attrs
    assert "description" in attrs


def test_mediahub_models_type_check_constraint_uses_type_enum():
    constraints = {c.name: c for c in MediahubModels.__table__.constraints if c.name}
    cc = constraints["mediahub_models_type_check"]
    sqltext = str(cc.sqltext)
    for value in ("llm", "embedding", "tts", "asr"):
        assert value in sqltext
    assert "mediahub_models_category_check" not in constraints
