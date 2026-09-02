"""``AssetsService.get_bundle`` — service level, no DB / no catalog / no network.

Exactly one thing is faked that the generate-slot tests do not fake:
``capabilities_for_model``, the catalog lookup. Everything else — the 404, the
loadout ownership check, the loadout-as-filter on prompts AND on files, the
image-availability stamp, the composition — runs for real, because those are
the parts that can be wrong quietly.

What the fake CANNOT tell you: whether the real lookup applies the same
visibility predicate the model picker uses. That is the whole point of
``capabilities_for_model`` living beside ``visible_generation_rows`` rather
than being re-derived here, and it is pinned in
``tests/test_model_capabilities_lookup.py``. A green run here is not evidence
that a hidden model is refused in production.
"""

from __future__ import annotations

import pytest

from app.schemas.assets import (
    AssetCreate,
    AttachFileRequest,
    LinkRequest,
    LoadoutCreate,
)
from app.services.ai.provider_protocols.base import ProviderCapabilities
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from tests.services.assets.test_assets_service import (
    SCOPE,
    USER,
    FakeAssetsRepo,
    FakeRelationsRepo,
)

MODEL = "mediahub-doubao-seedream-t2i"
SHEET_FILE = "727145299382534146"
WORN_FILE = "727145299382534147"
STILLS_FILE = "727145299382534148"
EXTRAS_FILE = "727145299382534149"


def _caps(max_refs: int) -> ProviderCapabilities:
    return ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=False,
        max_refs=max_refs,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )


@pytest.fixture
def svc():
    return AssetsService(
        assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo()
    )


@pytest.fixture
def catalog(monkeypatch):
    """Stub the model→capabilities lookup.

    ``models`` maps a catalog name to capabilities; a name that is absent
    resolves to None, which is what "disabled / hidden / no such model" looks
    like to the service. ``calls`` records the (model, user_id) pair so the
    user thread — the reason an owner-scoped row is visible to its owner and
    nobody else — is asserted rather than assumed.
    """

    class _Catalog:
        def __init__(self):
            self.models = {MODEL: _caps(3)}
            self.calls: list[tuple[str, str]] = []

        async def __call__(self, model, user_id):
            self.calls.append((str(model), str(user_id)))
            return self.models.get(str(model))

    fake = _Catalog()
    monkeypatch.setattr(assets_service, "capabilities_for_model", fake)
    return fake


async def _asset(svc, asset_type="character", name="Sang Yao", **extra):
    return await svc.create_asset(
        SCOPE, AssetCreate(asset_type=asset_type, name=name, **extra), USER
    )


async def _attach(svc, asset_id, resource_id, slot, **kw):
    svc.relations.in_scope_resources.add(int(resource_id))
    return await svc.attach_file(
        int(asset_id),
        SCOPE,
        AttachFileRequest(resource_id=str(resource_id), slot=slot, **kw),
        USER,
    )


async def _bundle(svc, asset_id, **kw):
    kw.setdefault("model", MODEL)
    kw.setdefault("user_id", USER)
    return await svc.get_bundle(int(asset_id), SCOPE, **kw)


# ── refusals ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_asset_outside_the_scope_is_404(svc, catalog):
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await svc.get_bundle(int(a["id"]), 999, model=MODEL, user_id=USER)
    assert e.value.status == 404 and e.value.code == "asset_not_found"


@pytest.mark.asyncio
async def test_an_unknown_model_is_422_model_unknown(svc, catalog):
    """A model the caller cannot use must not fall back to a default: the
    reference trim DEPENDS on the provider, so bundling with someone else's
    ceiling is a wrong answer delivered confidently."""
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await _bundle(svc, a["id"], model="not-a-model")
    assert e.value.status == 422 and e.value.code == "model_unknown"
    assert e.value.extra == {"model": "not-a-model"}


@pytest.mark.asyncio
async def test_a_loadout_from_another_asset_is_422(svc, catalog):
    a = await _asset(svc)
    other = await _asset(svc, name="Other")
    lo = await svc.create_loadout(int(other["id"]), SCOPE, LoadoutCreate(name="Gala"))
    with pytest.raises(AssetError) as e:
        await _bundle(svc, a["id"], loadout_id=lo["id"])
    assert e.value.status == 422 and e.value.code == "loadout_mismatch"


@pytest.mark.asyncio
async def test_the_loadout_is_checked_before_the_catalog(svc, catalog):
    """Both are 422s, so the order is invisible in the status — but a caller
    told ``model_unknown`` for a mismatched loadout goes hunting in the model
    picker. The subject of the request is checked first."""
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await _bundle(svc, a["id"], model="not-a-model", loadout_id="999999")
    assert e.value.code == "loadout_mismatch"


@pytest.mark.asyncio
async def test_a_system_preset_can_be_bundled(svc, catalog):
    """``_require``, not ``_require_writable``: composing writes nothing, and a
    preset is exactly the thing a canvas wants to deliver."""
    a = await _asset(svc, prompt_positive="a stock ronin")
    svc.assets.make_preset(a["id"])
    out = await _bundle(svc, a["id"])
    assert "a stock ronin" in out["prompt"]["positive"]


# ── happy path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bundle_carries_prompt_refs_dropped_and_the_ceiling(svc, catalog):
    a = await _asset(svc, prompt_positive="a swordswoman", prompt_negative="glasses")
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await _bundle(svc, a["id"])
    assert out["prompt"]["positive"] == "a swordswoman"
    assert out["prompt"]["negative"] == "glasses"
    assert out["reference_resource_ids"] == [SHEET_FILE]
    assert out["dropped"] == []
    assert out["max_refs"] == 3


@pytest.mark.asyncio
async def test_the_model_and_the_caller_both_reach_the_catalog(svc, catalog):
    """``user_id`` is not decoration: owner-scoped catalog rows (mig 431) are
    listed only for their owner, so a lookup that dropped it would show one
    user's private model to everyone."""
    a = await _asset(svc)
    await _bundle(svc, a["id"])
    assert catalog.calls == [(MODEL, USER)]


@pytest.mark.asyncio
async def test_the_provider_ceiling_wins_over_the_slot_constant(svc, catalog):
    """``MAX_SLOT_REFERENCES = 3`` governs generate-slot; the bundle must ask
    the provider. A codex bundle takes all four files."""
    a = await _asset(svc)
    for rid, slot in (
        (SHEET_FILE, "sheet"),
        (WORN_FILE, "stills"),
        (STILLS_FILE, "expressions"),
        (EXTRAS_FILE, "extras"),
    ):
        await _attach(svc, a["id"], rid, slot)
    catalog.models[MODEL] = _caps(9)

    out = await _bundle(svc, a["id"])

    assert len(out["reference_resource_ids"]) == 4
    assert out["dropped"] == [] and out["max_refs"] == 9


@pytest.mark.asyncio
async def test_a_zero_ref_provider_reports_every_reference(svc, catalog):
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    catalog.models[MODEL] = _caps(0)

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == []
    assert out["dropped"] == [{"resource_id": SHEET_FILE, "reason": "provider_no_refs"}]


@pytest.mark.asyncio
async def test_a_visible_model_with_no_protocol_is_not_model_unknown(svc, catalog):
    """``ProviderCapabilities.none()`` is a real answer — "this provider takes
    no references" — and must not be reported as a typo in the model name."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    catalog.models[MODEL] = ProviderCapabilities.none()

    out = await _bundle(svc, a["id"])

    assert out["max_refs"] == 0
    assert out["dropped"] == [{"resource_id": SHEET_FILE, "reason": "provider_no_refs"}]


# ── the loadout is a filter on both halves ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_loadout_filters_the_linked_costume_prompts(svc, catalog):
    a = await _asset(svc)
    worn = await _asset(
        svc, asset_type="costume", name="Gala Gown", prompt_positive="a silver gown"
    )
    unworn = await _asset(
        svc,
        asset_type="costume",
        name="Field Coat",
        prompt_positive="a muddy field coat",
    )
    for c in (worn, unworn):
        await svc.add_link(
            int(a["id"]), SCOPE, LinkRequest(to_asset_id=str(c["id"]), relation="wears")
        )
    lo = await svc.create_loadout(
        int(a["id"]),
        SCOPE,
        LoadoutCreate(
            name="Gala", costume_ids=[str(worn["id"])], prompt_extra="candlelight"
        ),
    )

    out = await _bundle(svc, a["id"], loadout_id=lo["id"])

    positive = out["prompt"]["positive"]
    assert "candlelight" in positive and "a silver gown" in positive
    assert "a muddy field coat" not in positive
    assert positive.index("candlelight") < positive.index("a silver gown")


@pytest.mark.asyncio
async def test_the_loadout_filters_the_reference_files_too(svc, catalog):
    """A file pinned to a DIFFERENT outfit must not become a reference — one
    plan describing two outfits is a costume in the picture the prompt
    deliberately left out."""
    a = await _asset(svc)
    mine = await svc.create_loadout(int(a["id"]), SCOPE, LoadoutCreate(name="Gala"))
    theirs = await svc.create_loadout(int(a["id"]), SCOPE, LoadoutCreate(name="Field"))
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], WORN_FILE, "worn", loadout_id=str(mine["id"]))
    await _attach(svc, a["id"], STILLS_FILE, "stills", loadout_id=str(theirs["id"]))

    out = await _bundle(svc, a["id"], loadout_id=mine["id"])

    assert out["reference_resource_ids"] == [SHEET_FILE, WORN_FILE]
    assert out["dropped"] == []


@pytest.mark.asyncio
async def test_without_a_loadout_only_the_unpinned_files_apply(svc, catalog):
    a = await _asset(svc)
    lo = await svc.create_loadout(int(a["id"]), SCOPE, LoadoutCreate(name="Gala"))
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], WORN_FILE, "worn", loadout_id=str(lo["id"]))

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == [SHEET_FILE]


@pytest.mark.asyncio
async def test_without_a_loadout_every_wears_and_holds_target_composes(svc, catalog):
    a = await _asset(svc)
    coat = await _asset(
        svc, asset_type="costume", name="Coat", prompt_positive="a long coat"
    )
    lamp = await _asset(
        svc, asset_type="prop", name="Lamp", prompt_positive="a brass lamp"
    )
    await svc.add_link(
        int(a["id"]), SCOPE, LinkRequest(to_asset_id=str(coat["id"]), relation="wears")
    )
    await svc.add_link(
        int(a["id"]), SCOPE, LinkRequest(to_asset_id=str(lamp["id"]), relation="holds")
    )

    positive = (await _bundle(svc, a["id"]))["prompt"]["positive"]

    assert positive.index("a long coat") < positive.index("a brass lamp")


@pytest.mark.asyncio
async def test_a_linked_assets_negative_joins_the_union(svc, catalog):
    a = await _asset(svc, prompt_negative="glasses")
    coat = await _asset(
        svc,
        asset_type="costume",
        name="Coat",
        prompt_positive="a long coat",
        prompt_negative="wrinkles",
    )
    await svc.add_link(
        int(a["id"]), SCOPE, LinkRequest(to_asset_id=str(coat["id"]), relation="wears")
    )

    negative = (await _bundle(svc, a["id"]))["prompt"]["negative"]

    assert negative.split(", ") == ["glasses", "wrinkles"]


# ── image availability ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_file_with_no_image_bytes_is_dropped_and_named(svc, catalog):
    """Same ladder ``generate_slot`` materializes through: an original that is
    not an image and no derived thumbnail means there is nothing to send."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], WORN_FILE, "worn")
    svc.relations.resource_media[int(WORN_FILE)] = {
        "file_path": "library/doc.pdf",
        "mime_type": "application/pdf",
        "thumbnail_path": None,
        "cover_image_path": None,
    }

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == [SHEET_FILE]
    assert out["dropped"] == [{"resource_id": WORN_FILE, "reason": "no_image_file"}]


@pytest.mark.asyncio
async def test_a_video_backed_file_rides_on_its_derived_cover(svc, catalog):
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    svc.relations.resource_media[int(SHEET_FILE)] = {
        "file_path": "library/clip.mp4",
        "mime_type": "video/mp4",
        "thumbnail_path": "library/clip.jpg",
        "cover_image_path": None,
    }

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == [SHEET_FILE] and out["dropped"] == []


@pytest.mark.asyncio
async def test_a_vanished_resource_row_is_reported_not_dropped_silently(svc, catalog):
    """Attached, then the resource was deleted. Reported as ``no_image_file``
    — this endpoint answers "what can be delivered", and there is nothing to
    deliver. The finer ``resource_not_found`` belongs to the RUN, which is
    where the user paid for the call."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    svc.relations.in_scope_resources.discard(int(SHEET_FILE))
    svc.relations.resource_media.clear()

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == []
    assert out["dropped"] == [{"resource_id": SHEET_FILE, "reason": "no_image_file"}]


@pytest.mark.asyncio
async def test_the_media_read_carries_its_own_audit_reason(svc, catalog):
    """``system_reason`` is the audit line a deliberate cross-user read logs.
    Sharing generate-slot's string would make a free bundle request
    indistinguishable from a paid generation in that log."""
    seen: list[str] = []
    original = svc.relations.resource_media_rows

    async def _spy(resource_ids, *, system_reason):
        seen.append(system_reason)
        return await original(resource_ids, system_reason=system_reason)

    svc.relations.resource_media_rows = _spy
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")

    await _bundle(svc, a["id"])

    assert seen == [assets_service._BUNDLE_READ_REASON]
    assert seen != [assets_service._REFERENCE_READ_REASON]


@pytest.mark.asyncio
async def test_an_asset_with_no_files_reads_no_media_rows(svc, catalog):
    """Nothing to look up is not a lookup of nothing: an empty IN () statement
    is a round trip bought for no answer."""
    calls: list[list] = []

    async def _spy(resource_ids, *, system_reason):
        calls.append(list(resource_ids))
        return {}

    svc.relations.resource_media_rows = _spy
    a = await _asset(svc)

    out = await _bundle(svc, a["id"])

    assert calls == []
    assert out["reference_resource_ids"] == [] and out["dropped"] == []


# ── the card's checklist reaches the trim (C1) ─────────────────────────────


@pytest.mark.asyncio
async def test_the_selection_narrows_the_population_before_the_ceiling(svc, catalog):
    """Four files, a ceiling of two, and the user picked the two that rank
    LAST. The old order delivered nothing and reported both picks as
    `over_limit`; the answer must be the two they picked."""
    a = await _asset(svc)
    for rid, slot in (
        (SHEET_FILE, "sheet"),
        (WORN_FILE, "worn"),
        (STILLS_FILE, "stills"),
        (EXTRAS_FILE, "extras"),
    ):
        await _attach(svc, a["id"], rid, slot)
    catalog.models[MODEL] = _caps(2)

    out = await _bundle(svc, a["id"], selected_file_ids=[STILLS_FILE, EXTRAS_FILE])

    assert out["reference_resource_ids"] == [STILLS_FILE, EXTRAS_FILE]
    assert out["dropped"] == []


@pytest.mark.asyncio
async def test_no_selection_leaves_the_sheets_answer_unchanged(svc, catalog):
    """The parameter is opt-in: the asset sheet never passes one and still
    gets every file the asset owns."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], WORN_FILE, "worn")
    catalog.models[MODEL] = _caps(9)

    out = await _bundle(svc, a["id"])

    assert out["reference_resource_ids"] == [SHEET_FILE, WORN_FILE]


@pytest.mark.asyncio
async def test_an_empty_selection_is_not_no_selection(svc, catalog):
    """Unticking every box must ship zero references — collapsing `[]` into
    `None` would ship exactly the files the user just removed."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    catalog.models[MODEL] = _caps(9)

    out = await _bundle(svc, a["id"], selected_file_ids=[])

    assert out["reference_resource_ids"] == [] and out["dropped"] == []


@pytest.mark.asyncio
async def test_the_loadout_filter_still_applies_within_a_selection(svc, catalog):
    """The two narrowings compose, and the loadout is the stricter one: a file
    pinned to another outfit is not deliverable however hard it is ticked."""
    a = await _asset(svc)
    mine = await svc.create_loadout(int(a["id"]), SCOPE, LoadoutCreate(name="Gala"))
    theirs = await svc.create_loadout(int(a["id"]), SCOPE, LoadoutCreate(name="Field"))
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], WORN_FILE, "worn", loadout_id=str(mine["id"]))
    await _attach(svc, a["id"], STILLS_FILE, "stills", loadout_id=str(theirs["id"]))
    catalog.models[MODEL] = _caps(9)

    out = await _bundle(
        svc,
        a["id"],
        loadout_id=mine["id"],
        selected_file_ids=[WORN_FILE, STILLS_FILE],
    )

    assert out["reference_resource_ids"] == [WORN_FILE]
    assert out["dropped"] == []
