"""Asset Library ORM/repository serialization tests (mig 445)."""

import datetime

from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    CanvasAssetRefs,
)
from app.repositories.assets_repository import (
    _BIGINT_COLS,
    DuplicateAssetName,
    _serialize,
    with_derived,
)


def test_asset_models_map_expected_tables():
    assert Assets.__tablename__ == "assets"
    assert AssetLoadouts.__tablename__ == "asset_loadouts"
    assert AssetFiles.__tablename__ == "asset_files"
    assert AssetLinks.__tablename__ == "asset_links"
    assert AssetProjectRefs.__tablename__ == "asset_project_refs"
    assert CanvasAssetRefs.__tablename__ == "canvas_asset_refs"
    # asset_files has FK to asset_loadouts — creation order dependency
    fk_targets = {fk.column.table.name for fk in AssetFiles.__table__.foreign_keys}
    assert fk_targets == {"assets", "resources", "asset_loadouts"}


def test_serialize_stringifies_every_bigint_and_isoformats_datetimes():
    now = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)
    row = {
        "id": 727145299382534145,
        "scope_id": 727145299382534200,
        "cover_file_id": None,
        "duplicated_from": 727145299382534201,
        "created_by": "11111111-1111-1111-1111-111111111111",
        "created_at": now,
        "attrs": {"k": 1},
        "name": "Sang Yao",
    }
    out = _serialize(row)
    assert out["id"] == "727145299382534145"
    assert out["duplicated_from"] == "727145299382534201"
    assert out["cover_file_id"] is None
    assert out["created_at"] == now.isoformat()
    assert out["attrs"] == {"k": 1}
    assert set(_BIGINT_COLS) >= {"id", "scope_id", "cover_file_id", "duplicated_from"}


def test_with_derived_attaches_readiness_counts_projects_loadouts():
    row = {"id": 5, "asset_type": "character", "prompt_positive": None}
    out = with_derived(
        row,
        slot_counts={5: {"sheet": 1, "stills": 3}},
        project_ids={5: [900, 901]},
        loadout_counts={5: 2},
    )
    assert out["readiness"] == {"state": "ready", "missing": []}
    assert out["file_counts_by_slot"] == {"sheet": 1, "stills": 3}
    assert out["project_ids"] == ["900", "901"]
    assert out["loadout_count"] == 2


def test_with_derived_missing_maps_default_to_draft():
    out = with_derived(
        {"id": 6, "asset_type": "prop", "prompt_positive": None}, {}, {}, {}
    )
    assert out["readiness"] == {"state": "draft", "missing": ["turnaround"]}
    assert out["project_ids"] == [] and out["loadout_count"] == 0


def test_duplicate_asset_name_carries_existing_id():
    err = DuplicateAssetName(existing_id=42)
    assert err.existing_id == 42 and "42" in str(err)
