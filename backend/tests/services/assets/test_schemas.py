from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.models.assets import (
    ASSET_SOURCES,
    ASSET_TYPES,
    LINK_RELATIONS,
    AssetLoadouts,
    Assets,
)
from app.schemas.assets import (
    AssetCountsResponse,
    AssetCreate,
    AssetDetailResponse,
    AssetFileResponse,
    AssetLinkResponse,
    AssetResponse,
    AssetSource,
    AssetType,
    AssetUpdate,
    AttachFileRequest,
    DuplicateRequest,
    Envelope,
    ErrorEnvelope,
    LinkRelation,
    LinkRequest,
    LoadoutCreate,
    LoadoutResponse,
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
        # Required since mig 449 — the serializer emits every column, so a
        # response built without it is a shape the wire never produces.
        in_library=True,
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
        (DuplicateRequest, {"nmae": "Sang Yao copy"}),
    ],
)
def test_patch_models_forbid_unknown_fields(model, payload):
    """Without extra="forbid" these returned 200 with the row untouched — a
    typo'd field indistinguishable from a successful edit."""
    with pytest.raises(ValidationError) as ei:
        model(**payload)
    assert "extra" in str(ei.value).lower()


# ── duplicate: the only knob is the new name ───────────────────────────────


def test_duplicate_request_name_is_optional_and_bounded():
    """Omitted name = "{source} (copy)", decided by the service. An empty
    string is NOT that default — it would be a nameless asset — so it is a 422
    like every other blank name on this surface."""
    assert DuplicateRequest().name is None
    assert DuplicateRequest(name="Sang Yao (v2)").name == "Sang Yao (v2)"
    with pytest.raises(ValidationError):
        DuplicateRequest(name="")
    with pytest.raises(ValidationError):
        DuplicateRequest(name="x" * 201)


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


# ── P2: the response envelope is a model, not a hand-rolled dict ───────────


def test_envelope_defaults_to_success_and_carries_typed_data():
    env = Envelope[AssetFileResponse](
        data={
            "asset_id": "1",
            "resource_id": "2",
            "slot": "sheet",
            "attached_at": datetime(2026, 8, 29, 12, 0, 0),
        }
    )
    assert env.success is True
    assert env.data.slot == "sheet"


def test_error_envelope_defaults_to_failure():
    err = ErrorEnvelope(error={"code": "not_a_member", "detail": "nope"})
    assert err.success is False
    assert err.error["code"] == "not_a_member"


def test_file_and_link_models_carry_every_field_the_serializers_emit():
    """``response_model`` DROPS undeclared keys silently. ``_serialize_file``
    emits ``attached_by`` and ``_serialize_link`` emits ``created_at``; if the
    models omitted them the fields would vanish from the wire the day the
    envelope landed — a removal nothing would report."""
    from app.repositories.asset_relations_repository import (
        _serialize_file,
        _serialize_link,
    )

    now = datetime(2026, 8, 29, 12, 0, 0)
    file_keys = set(
        _serialize_file(
            {
                "asset_id": 1,
                "resource_id": 2,
                "slot": "sheet",
                "loadout_id": None,
                "sort_order": 0,
                "note": None,
                "attached_by": None,
                "attached_at": now,
            }
        )
    )
    link_keys = set(
        _serialize_link(
            {
                "from_asset_id": 1,
                "to_asset_id": 2,
                "relation": "wears",
                "created_at": now,
            }
        )
    )
    assert file_keys <= set(AssetFileResponse.model_fields)
    assert link_keys <= set(AssetLinkResponse.model_fields)


def test_asset_response_declares_every_asset_column_and_derived_key():
    """The same silent-drop guard, for the asset row itself.

    ``_serialize`` emits EVERY ``assets`` column, and ``with_derived`` adds four
    keys on top — but ``Envelope[AssetResponse]`` only forwards what the model
    declares. Without this test, the next migration that adds a column to
    ``assets`` would have the repo emit it, the response model drop it, the
    frontend never see it, and nothing anywhere fail.

    ``deleted_at`` is the single deliberate exclusion: rows reaching a response
    are the live ones (every read filters ``deleted_at IS NULL``), so the field
    is always null and carries no information.
    """
    emitted = ({c.name for c in Assets.__table__.columns} - {"deleted_at"}) | {
        "readiness",
        "file_counts_by_slot",
        "project_ids",
        "loadout_count",
    }
    assert emitted <= set(AssetResponse.model_fields)


def test_loadout_response_declares_every_loadout_column():
    """Same guard for ``asset_loadouts`` — ``_serialize_loadout`` emits the whole
    row, and the create/update/detail routes answer with this model."""
    emitted = {c.name for c in AssetLoadouts.__table__.columns}
    assert emitted <= set(LoadoutResponse.model_fields)


# ── P2: omit = unchanged, explicit null = clear ────────────────────────────


def test_asset_update_distinguishes_omitted_from_explicit_null():
    """``exclude_none`` cannot tell the two apart — which is why clearing a
    field was unreachable through PATCH before P2."""
    omitted = AssetUpdate(name="Sang Yao")
    cleared = AssetUpdate(name="Sang Yao", subtype=None)
    assert "subtype" not in omitted.model_fields_set
    assert "subtype" in cleared.model_fields_set
    assert cleared.model_dump(exclude_unset=True) == {
        "name": "Sang Yao",
        "subtype": None,
    }


def test_asset_update_docstring_states_the_null_semantics():
    """The semantics live where the payload is declared, not in one call site."""
    doc = (AssetUpdate.__doc__ or "").lower()
    assert "omit" in doc and "null" in doc and "clear" in doc


# ── P2: the counts payload carries one field per asset type ────────────────


def test_asset_counts_response_covers_exactly_the_asset_types():
    """The sidebar renders one badge per key of this model.

    A type added to ``ASSET_TYPES`` but not here would be counted by the
    repository and then DROPPED by the response model on the way out — the
    library would gain a type the sidebar cannot show, with nothing failing.
    """
    assert set(AssetCountsResponse.model_fields) == set(ASSET_TYPES)


def test_asset_counts_response_defaults_every_type_to_zero():
    """A type the repository omitted must read as 0, not as a missing key: the
    client branches on `count > 0` to decide whether to draw a badge, and
    `undefined > 0` is a silently different answer from `0 > 0`."""
    out = AssetCountsResponse().model_dump()
    assert out == {t: 0 for t in ASSET_TYPES}


def test_asset_counts_response_rejects_a_non_integer_tally():
    """Response-model validation is the point of declaring it — a service that
    answered with a string must fail loudly, not ship a NaN badge."""
    with pytest.raises(ValidationError):
        AssetCountsResponse(character="many")


# ── mig 449: explicit library membership ───────────────────────────────────


def test_the_response_carries_membership_and_the_patch_body_does_not():
    """The response carries it because a client must never have to infer
    membership from ``source``.

    ``AssetUpdate`` does NOT, and that asymmetry is the design: membership has
    one write path (``POST``/``DELETE /assets/{id}/library``), and declaring the
    field here would add a second that converges only at the repository.
    """
    assert "in_library" in AssetResponse.model_fields
    assert "in_library" not in AssetUpdate.model_fields


def test_a_patch_aimed_at_membership_is_a_typed_refusal():
    """``extra="forbid"`` is what turns the omission above into an ANSWER.
    Without it the key would be silently dropped and the request would report
    200 having changed nothing — the silent-no-op class this module refuses."""
    with pytest.raises(ValidationError) as e:
        AssetUpdate(name="Sang Yao", in_library=True)
    assert "in_library" in str(e.value)


def test_the_response_requires_membership_rather_than_defaulting_it():
    """No default, unlike its neighbours.

    This model is what FastAPI validates on the way out, so a default would let
    a service that stopped emitting the key ship a row every client reads as "in
    library". Membership decides whether the shelf shows the asset at all —
    guessing it is worse than failing loudly.
    """
    assert AssetResponse.model_fields["in_library"].is_required()

    with pytest.raises(ValidationError):
        AssetResponse(
            id="1",
            asset_type="character",
            name="Sang Yao",
            created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            updated_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            readiness={"state": "draft", "missing": []},
        )


def test_a_client_cannot_set_membership_at_creation():
    """``AssetCreate`` deliberately has no ``in_library``: membership at
    creation is decided by WHICH SERVER PATH created the row (deliberate act →
    in, script import / migration → out), and that is a keyword-only argument
    on ``create_asset`` no request body can reach — the same stance
    ``AssetCreateSource`` takes for provenance.

    ``extra`` is not forbidden on ``AssetCreate``, so the field would be
    silently dropped rather than refused; this asserts the model does not
    declare it at all.
    """
    assert "in_library" not in AssetCreate.model_fields
    made = AssetCreate(asset_type="character", name="Sang Yao", in_library=False)
    assert not hasattr(made, "in_library")
