"""Asset Library ORM/repository serialization tests (mig 445)."""

from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    CanvasAssetRefs,
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
