"""mig 453: the ORM must carry the column AND its CHECK, or schema-drift goes red."""

from sqlalchemy import CheckConstraint

from app.models import Resources


def test_prompt_origin_column_exists_and_is_nullable_text():
    col = Resources.__table__.c["prompt_origin"]
    assert col.nullable is True
    assert col.type.python_type is str


def test_prompt_origin_check_constraint_is_declared():
    names = {
        c.name
        for c in Resources.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert "resources_prompt_origin_check" in names
