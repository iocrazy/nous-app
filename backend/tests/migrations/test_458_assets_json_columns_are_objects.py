"""mig 458 — the assets jsonb columns must hold objects.

NOT NULL DEFAULT '{}' does not stop the JSON value ``null``; one hand-seeded
preset carrying it took the asset shelf and the chat @-picker down for every
user (2026-09-06). Pins the migration text and the ORM CheckConstraint so the
two cannot drift, and the response-model validator that keeps a single bad
row from failing a whole listing on the way out.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint

from app.models.assets import Assets
from app.schemas.assets import AssetResponse

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "458_assets_json_columns_are_objects.sql"
)


def test_migration_repairs_then_enforces():
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "UPDATE public.assets" in sql
    assert "ADD CONSTRAINT assets_json_columns_are_objects" in sql
    for col in ("attrs", "platform_params", "tags"):
        assert f"jsonb_typeof({col}) = 'object'" in sql
    # Repair precedes enforcement: a constraint added first would fail on the
    # very row it exists to prevent.
    assert sql.index("UPDATE public.assets") < sql.index("ADD CONSTRAINT")


def test_orm_check_mirrors_the_migration():
    check = next(
        c
        for c in Assets.__table__.constraints
        if isinstance(c, CheckConstraint)
        and c.name == "assets_json_columns_are_objects"
    )
    text = str(check.sqltext)
    for col in ("attrs", "platform_params", "tags"):
        assert f"jsonb_typeof({col}) = 'object'" in text


def _row(**overrides):
    base = {
        "id": "1",
        "asset_type": "prompt",
        "name": "x",
        "in_library": True,
        "created_at": "2026-09-06T00:00:00Z",
        "updated_at": "2026-09-06T00:00:00Z",
        "readiness": {"state": "ready", "missing": []},
    }
    base.update(overrides)
    return base


def test_response_model_reads_json_null_as_empty_object():
    out = AssetResponse.model_validate(
        _row(platform_params=None, attrs=None, tags=None)
    )
    assert out.platform_params == {} and out.attrs == {} and out.tags == {}


def test_response_model_still_refuses_a_non_object():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AssetResponse.model_validate(_row(platform_params=["not", "a", "dict"]))
