from datetime import datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from app.models.assets import ASSET_SOURCES, ASSET_TYPES, LINK_RELATIONS
from app.schemas.assets import (
    AssetCreate,
    AssetDetailResponse,
    AssetResponse,
    AssetSource,
    AssetType,
    AssetUpdate,
    AttachFileRequest,
    LinkRelation,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
    ProjectRefRequest,
)


def test_asset_create_requires_name_and_valid_type():
    a = AssetCreate(asset_type="character", name="Sang Yao")
    assert a.role_tag == "" and a.attrs == {} and a.tags == {}
    with pytest.raises(ValidationError):
        AssetCreate(asset_type="video", name="x")
    with pytest.raises(ValidationError):
        AssetCreate(asset_type="prop", name="")


def test_asset_update_none_means_unchanged():
    u = AssetUpdate(description="new")
    assert u.model_dump(exclude_none=True) == {"description": "new"}


def test_attach_file_request_defaults_unsorted():
    r = AttachFileRequest(resource_id="727145299382534145")
    assert r.slot == "unsorted" and r.loadout_id is None


def test_loadout_create_ids_are_strings():
    lo = LoadoutCreate(name="Night raid", costume_ids=["1"], prop_ids=[])
    assert lo.costume_ids == ["1"]


def test_attach_file_request_rejects_blank_slot():
    with pytest.raises(ValidationError):
        AttachFileRequest(resource_id="727145299382534145", slot="")


def test_asset_response_allows_null_scope_for_system_presets():
    """System-preset rows carry scope_id IS NULL (assets_scope_or_preset CHECK).

    A required scope_id would raise inside the router the first time a list or
    detail response included a preset — a 500, not a typed failure.
    """
    now = datetime(2026, 8, 28, 12, 0, 0)
    r = AssetResponse(
        id="727145299382534145",
        scope_id=None,
        asset_type="prop",
        name="Preset Lantern",
        is_system_preset=True,
        source="system_preset",
        created_at=now,
        updated_at=now,
        readiness={"state": "draft", "missing": ["primary"]},
    )
    assert r.scope_id is None
    assert AssetDetailResponse.model_fields["scope_id"].is_required() is False


def test_snowflake_id_fields_reject_non_numeric():
    """Every request-side id is int()-ed downstream; a non-numeric value has to
    fail here as a 422, not as a ValueError inside the service (a 500)."""
    with pytest.raises(ValidationError):
        AttachFileRequest(resource_id="abc")
    with pytest.raises(ValidationError):
        AttachFileRequest(resource_id="727145299382534145", loadout_id="abc")
    with pytest.raises(ValidationError):
        LoadoutCreate(name="Night", costume_ids=["abc"])
    with pytest.raises(ValidationError):
        LoadoutUpdate(prop_ids=["12x"])
    with pytest.raises(ValidationError):
        LinkRequest(to_asset_id="", relation="wears")
    with pytest.raises(ValidationError):
        ProjectRefRequest(project_id="55; DROP TABLE assets")
    with pytest.raises(ValidationError):
        AssetUpdate(cover_file_id="not-an-id")


def test_snowflake_id_fields_accept_digit_strings():
    """Positive control — the pattern must not reject the real wire shape."""
    assert AttachFileRequest(resource_id="727145299382534145").resource_id == (
        "727145299382534145"
    )
    assert LoadoutCreate(
        name="Night", costume_ids=["1", "727145299382534145"]
    ).costume_ids == [
        "1",
        "727145299382534145",
    ]
    assert (
        LinkRequest(to_asset_id="727145299382534146", relation="wears").relation
        == "wears"
    )
    assert ProjectRefRequest(project_id="55").project_id == "55"
    assert AssetUpdate(cover_file_id="727145299382534147").cover_file_id == (
        "727145299382534147"
    )


def test_literals_match_model_constants():
    assert set(get_args(AssetType)) == set(ASSET_TYPES)
    assert set(get_args(LinkRelation)) == set(LINK_RELATIONS)
    assert set(get_args(AssetSource)) == set(ASSET_SOURCES)


# ── I3: a typo'd PATCH field must not read as a successful edit ────────────


@pytest.mark.parametrize(
    "model,payload",
    [
        (AssetUpdate, {"promptpositive": "a moonlit rooftop"}),
        (AssetUpdate, {"prompt_postive": "typo"}),
        (LoadoutUpdate, {"costumeids": ["1"]}),
        (LoadoutUpdate, {"isdefault": True}),
    ],
)
def test_patch_models_forbid_unknown_fields(model, payload):
    """Without extra="forbid" these returned 200 with the row untouched — a
    typo'd field indistinguishable from a successful edit."""
    with pytest.raises(ValidationError) as ei:
        model(**payload)
    assert "extra" in str(ei.value).lower()


# ── M1: ids must fit BIGINT, not just "20 digits" ──────────────────────────


_TOO_BIG = str(2**63)  # 19 digits, first value BIGINT cannot hold
_MAX_OK = str(2**63 - 1)


@pytest.mark.parametrize(
    "model,field",
    [
        (AttachFileRequest, "resource_id"),
        (LinkRequest, "to_asset_id"),
        (ProjectRefRequest, "project_id"),
        (AssetUpdate, "cover_file_id"),
    ],
)
def test_snowflake_ids_are_bounded_to_int64(model, field):
    """``^[0-9]{1,20}$`` alone admits ~10x int64; those parse and then blow up at
    driver BIND — a 500 for hostile input. The bound belongs at validation."""
    base = {"relation": "wears"} if model is LinkRequest else {}
    with pytest.raises(ValidationError):
        model(**{**base, field: "99999999999999999999"})  # 20 digits
    with pytest.raises(ValidationError):
        model(**{**base, field: _TOO_BIG})  # exactly 2**63
    assert model(**{**base, field: _MAX_OK})  # 2**63-1 still accepted


def test_loadout_id_lists_are_bounded_too():
    with pytest.raises(ValidationError):
        LoadoutCreate(name="Night", costume_ids=["99999999999999999999"])
    assert LoadoutCreate(name="Night", costume_ids=[_MAX_OK])
