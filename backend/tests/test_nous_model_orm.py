# backend/tests/test_nous_model_orm.py
"""NousModels ORM model reflects migration 302 (type column + description)."""

from app.models import NousModels


def test_nous_models_has_type_column_not_category():
    attrs = {p.key for p in NousModels.__mapper__.column_attrs}
    assert "type" in attrs
    assert "category" not in attrs
    assert "description" in attrs


def test_nous_models_type_check_constraint_uses_type_enum():
    constraints = {c.name: c for c in NousModels.__table__.constraints if c.name}
    cc = constraints["nous_models_type_check"]
    sqltext = str(cc.sqltext)
    for value in ("llm", "embedding", "tts", "asr"):
        assert value in sqltext
    assert "nous_models_category_check" not in constraints
