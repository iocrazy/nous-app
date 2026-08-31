"""P2 service behaviour (spec §5.1 follow-ups), on the same in-memory fakes.

Four additions, each of which used to have a silent failure mode:

* **explicit null clears** — PATCH could only ever SET a value, so "remove this
  asset's cover / subtype / prompt" was unreachable through the API. ``None``
  meant "unchanged", which is also what an omitted key means, so a client
  asking to clear got 200 and no change.
* **cover_file_id scope check** — the id went straight into the column, so a
  caller could point their asset's cover at a resource belonging to another
  team (a 200 for a cross-tenant reference).
* **updated_at touch** — ``assets`` deliberately ships NO touch trigger
  (mig 445), so attaching a file left the row's ``updated_at`` stale and the
  "recent" shelf ordering did not move when the asset visibly changed.
* **readiness filter / sort** — derived per row, so it can only be applied
  after ``_derived``; the repo cannot do it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    DuplicateRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
)
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from tests.services.assets.test_assets_service import (
    SCOPE,
    USER,
    FakeAssetsRepo,
    FakeRelationsRepo,
)

IN_SCOPE_RESOURCE = "727145299382534146"


@pytest.fixture
def svc():
    return AssetsService(
        assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo()
    )


async def _character(svc, name="Sang Yao", **extra):
    return await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name=name, **extra), USER
    )


# ── explicit null = clear, omitted = unchanged ─────────────────────────────


@pytest.mark.asyncio
async def test_explicit_null_clears_a_clearable_field(svc):
    a = await _character(svc, subtype="lead", prompt_positive="a rooftop at dusk")
    out = await svc.update_asset(
        int(a["id"]), SCOPE, AssetUpdate(subtype=None, prompt_positive=None)
    )
    assert out["subtype"] is None
    assert out["prompt_positive"] is None


@pytest.mark.asyncio
async def test_omitted_field_is_left_unchanged(svc):
    a = await _character(svc, subtype="lead")
    out = await svc.update_asset(
        int(a["id"]), SCOPE, AssetUpdate(description="Night, neon")
    )
    assert out["subtype"] == "lead", "an omitted key must not be written"
    assert out["description"] == "Night, neon"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "subtype",
        "prompt_positive",
        "prompt_negative",
        "prompt_positive_zh",
        "prompt_negative_zh",
        "cover_file_id",
    ],
)
async def test_every_clearable_field_accepts_an_explicit_null(svc, field):
    a = await _character(svc, name=f"Clearable {field}")
    out = await svc.update_asset(int(a["id"]), SCOPE, AssetUpdate(**{field: None}))
    assert out[field] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["name", "role_tag", "description", "attrs", "platform_params", "tags"]
)
async def test_null_on_a_not_null_column_is_a_typed_422(svc, field):
    """These columns are NOT NULL. Writing the null would be an IntegrityError
    (a 500); dropping it silently would be the no-op class this module refuses.
    """
    a = await _character(svc, name=f"NotNull {field}")
    with pytest.raises(AssetError) as ei:
        await svc.update_asset(int(a["id"]), SCOPE, AssetUpdate(**{field: None}))
    assert ei.value.status == 422 and ei.value.code == "field_not_nullable"
    assert ei.value.extra["fields"] == [field]


# ── cover_file_id must be a resource of THIS scope ─────────────────────────


@pytest.mark.asyncio
async def test_cover_file_outside_the_scope_is_404(svc):
    a = await _character(svc)
    with pytest.raises(AssetError) as ei:
        await svc.update_asset(
            int(a["id"]), SCOPE, AssetUpdate(cover_file_id="999888777")
        )
    assert ei.value.status == 404 and ei.value.code == "resource_not_found"


@pytest.mark.asyncio
async def test_cover_file_in_scope_is_stored_as_an_int(svc):
    a = await _character(svc)
    out = await svc.update_asset(
        int(a["id"]), SCOPE, AssetUpdate(cover_file_id=IN_SCOPE_RESOURCE)
    )
    assert out["cover_file_id"] == IN_SCOPE_RESOURCE  # serialized back to a string
    assert svc.assets.rows[int(a["id"])]["cover_file_id"] == int(IN_SCOPE_RESOURCE)
    assert svc.relations.scope_checks == [(int(IN_SCOPE_RESOURCE), SCOPE)]


@pytest.mark.asyncio
async def test_clearing_the_cover_asks_no_scope_question(svc):
    """A null is a removal — there is no resource to be in scope. With an empty
    in-scope set, a check would 404 and this would fail."""
    a = await _character(svc)
    svc.relations.in_scope_resources = set()
    out = await svc.update_asset(int(a["id"]), SCOPE, AssetUpdate(cover_file_id=None))
    assert out["cover_file_id"] is None
    assert svc.relations.scope_checks == []


# ── every relation write touches assets.updated_at ─────────────────────────


async def _seed_linkable(svc):
    """A character + the costume/prop it is allowed to wear/hold."""
    ch = await _character(svc, name="Touch Subject")
    costume = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Night Cloak"), USER
    )
    prop = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Jade Blade"), USER
    )
    return int(ch["id"]), int(costume["id"]), int(prop["id"])


@pytest.mark.asyncio
async def test_attach_and_detach_touch_the_asset(svc):
    aid, _, _ = await _seed_linkable(svc)
    svc.relations.touched.clear()
    await svc.attach_file(
        aid, SCOPE, AttachFileRequest(resource_id=IN_SCOPE_RESOURCE, slot="sheet"), USER
    )
    assert svc.relations.touched == [aid]
    await svc.detach_file(aid, SCOPE, int(IN_SCOPE_RESOURCE), "sheet")
    assert svc.relations.touched == [aid, aid]


@pytest.mark.asyncio
async def test_link_and_unlink_touch_the_asset(svc):
    aid, costume_id, _ = await _seed_linkable(svc)
    svc.relations.touched.clear()
    await svc.add_link(
        aid, SCOPE, LinkRequest(to_asset_id=str(costume_id), relation="wears")
    )
    assert svc.relations.touched == [aid]
    await svc.remove_link(aid, SCOPE, costume_id, "wears")
    assert svc.relations.touched == [aid, aid]


@pytest.mark.asyncio
async def test_loadout_create_update_delete_touch_the_asset(svc):
    aid, costume_id, _ = await _seed_linkable(svc)
    await svc.add_link(
        aid, SCOPE, LinkRequest(to_asset_id=str(costume_id), relation="wears")
    )
    svc.relations.touched.clear()
    lo = await svc.create_loadout(aid, SCOPE, LoadoutCreate(name="Night raid"))
    assert svc.relations.touched == [aid]
    await svc.update_loadout(
        aid, SCOPE, int(lo["id"]), LoadoutUpdate(costume_ids=[str(costume_id)])
    )
    assert svc.relations.touched == [aid, aid]
    await svc.delete_loadout(aid, SCOPE, int(lo["id"]))
    assert svc.relations.touched == [aid, aid, aid]


@pytest.mark.asyncio
async def test_project_link_and_unlink_touch_the_asset(svc):
    aid, _, _ = await _seed_linkable(svc)
    svc.relations.touched.clear()
    await svc.link_project(aid, SCOPE, 55, USER)
    assert svc.relations.touched == [aid]
    await svc.unlink_project(aid, SCOPE, 55)
    assert svc.relations.touched == [aid, aid]


@pytest.mark.asyncio
async def test_a_refused_write_touches_nothing(svc):
    """The bump reports "this asset changed". A 404/422 changed nothing."""
    aid, _, _ = await _seed_linkable(svc)
    svc.relations.touched.clear()
    with pytest.raises(AssetError):
        await svc.detach_file(aid, SCOPE, int(IN_SCOPE_RESOURCE), "sheet")
    with pytest.raises(AssetError):
        await svc.remove_link(aid, SCOPE, 424242, "wears")
    with pytest.raises(AssetError):
        await svc.unlink_project(aid, SCOPE, 55)
    assert svc.relations.touched == []


# ── readiness filter + sort (derived, so the service owns them) ────────────


async def _two_prompts(svc):
    ready = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type="prompt", name="Ready One", prompt_positive="a body"),
        USER,
    )
    draft = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prompt", name="Draft One"), USER
    )
    return ready["id"], draft["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ready", "draft"])
async def test_readiness_filter_keeps_only_that_state(svc, state):
    ready_id, draft_id = await _two_prompts(svc)
    rows = await svc.list_assets(SCOPE, readiness=state)
    assert [r["id"] for r in rows] == [ready_id if state == "ready" else draft_id]
    assert all(r["readiness"]["state"] == state for r in rows)


@pytest.mark.asyncio
async def test_no_readiness_filter_returns_both(svc):
    ready_id, draft_id = await _two_prompts(svc)
    rows = await svc.list_assets(SCOPE)
    assert {r["id"] for r in rows} == {ready_id, draft_id}


@pytest.mark.asyncio
async def test_sort_readiness_puts_drafts_first_and_never_reaches_sql(svc):
    """``readiness`` is derived, so the repo cannot order by it — the service
    must translate the request into an ordering SQL *can* do and re-sort after
    deriving. Passing 'readiness' down would be an invalid ORDER BY."""
    ready_id, draft_id = await _two_prompts(svc)
    rows = await svc.list_assets(SCOPE, sort="readiness")
    assert [r["id"] for r in rows] == [draft_id, ready_id]
    assert svc.assets.list_calls[-1]["sort"] == "recent"


@pytest.mark.asyncio
@pytest.mark.parametrize("sort", ["recent", "name"])
async def test_sql_sorts_are_delegated_unchanged(svc, sort):
    await _two_prompts(svc)
    await svc.list_assets(SCOPE, sort=sort)
    assert svc.assets.list_calls[-1]["sort"] == sort


@pytest.mark.asyncio
async def test_tag_filter_is_delegated_to_sql(svc):
    """A JSONB containment question belongs in the query, not in Python — the
    service must not quietly re-implement (or drop) it."""
    await _two_prompts(svc)
    await svc.list_assets(SCOPE, tag="hero")
    assert svc.assets.list_calls[-1]["tag"] == "hero"


# ── duplicate (P2 Task 2) ──────────────────────────────────────────────────
# A copy is only useful if it is a WHOLE copy: the header fields, the files at
# their slots, the loadouts with their default, and the loadout each file
# belongs to. The loadout remap is the part that silently degrades — copying
# asset_files verbatim would leave the new asset's files pointing at the
# SOURCE's loadout rows, so the copy would look complete and behave as if its
# costumes belonged to someone else.

R_WORN = "727145299382534147"
COVER = "727145299382534148"


async def _duplicable_character(svc):
    """A character carrying one of everything the copy has to decide about.

    Note the default is deliberately NOT the first loadout created: the fake
    ``list_loadouts`` returns insertion order (the real one sorts default
    first), so a service that copies in list order would create a non-default
    row first — and against Postgres that is fine only by luck. Ordering the
    default first is asserted below.
    """
    svc.relations.in_scope_resources |= {int(R_WORN), int(COVER)}
    ch = await _character(svc, name="Sang Yao")
    aid = int(ch["id"])
    costume = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Night Cloak"), USER
    )
    await svc.add_link(
        aid, SCOPE, LinkRequest(to_asset_id=costume["id"], relation="wears")
    )
    night = await svc.create_loadout(
        aid, SCOPE, LoadoutCreate(name="Night raid", costume_ids=[costume["id"]])
    )
    await svc.update_loadout(
        aid, SCOPE, int(night["id"]), LoadoutUpdate(is_default=True)
    )
    await svc.attach_file(
        aid, SCOPE, AttachFileRequest(resource_id=IN_SCOPE_RESOURCE, slot="sheet"), USER
    )
    await svc.attach_file(
        aid,
        SCOPE,
        AttachFileRequest(resource_id=R_WORN, slot="worn", loadout_id=night["id"]),
        USER,
    )
    # An INCOMING link (audio voices this character) — must not be copied.
    audio = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type="audio", name="Sang Yao VO", subtype="voice"),
        USER,
    )
    await svc.add_link(
        int(audio["id"]), SCOPE, LinkRequest(to_asset_id=ch["id"], relation="voice_of")
    )
    await svc.link_project(aid, SCOPE, 55, USER)
    await svc.update_asset(aid, SCOPE, AssetUpdate(cover_file_id=COVER))
    return await svc.get_asset(aid, SCOPE)


@pytest.mark.asyncio
async def test_duplicate_copies_the_header_and_marks_provenance(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert out["name"] == "Sang Yao (copy)"
    assert out["source"] == "duplicated"
    assert out["duplicated_from"] == src["id"]
    assert out["is_system_preset"] is False
    assert out["scope_id"] == str(SCOPE)
    assert out["id"] != src["id"]
    for field in (
        "asset_type",
        "subtype",
        "role_tag",
        "description",
        "attrs",
        "prompt_positive",
        "prompt_negative",
        "prompt_positive_zh",
        "prompt_negative_zh",
        "platform_params",
        "tags",
    ):
        assert out[field] == src[field], field


@pytest.mark.asyncio
async def test_duplicate_honours_an_explicit_name(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(
        int(src["id"]), SCOPE, USER, DuplicateRequest(name="Sang Yao (v2)")
    )
    assert out["name"] == "Sang Yao (v2)"


@pytest.mark.asyncio
async def test_duplicate_copies_the_mutable_json_by_value(svc):
    """attrs/tags/platform_params are dicts. Handing the new row the SAME object
    would make an edit to the copy silently rewrite the original."""
    src = await _character(
        svc, name="Shared Json", attrs={"height": "tall"}, tags={"role": ["hero"]}
    )
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    svc.assets.rows[int(out["id"])]["attrs"]["height"] = "short"
    svc.assets.rows[int(out["id"])]["tags"]["role"].append("villain")
    assert svc.assets.rows[int(src["id"])]["attrs"] == {"height": "tall"}
    assert svc.assets.rows[int(src["id"])]["tags"] == {"role": ["hero"]}


@pytest.mark.asyncio
async def test_duplicate_of_a_preset_lands_in_the_callers_scope_as_an_editable_row(svc):
    """Duplicating IS how a preset gets edited (_require_writable refuses every
    write to one), so this path must be open to any member — and the result must
    be a normal, writable row of the caller's scope."""
    preset = await _character(svc, name="Preset Hero")
    svc.assets.make_preset(int(preset["id"]))
    out = await svc.duplicate(int(preset["id"]), SCOPE, USER, DuplicateRequest())
    assert out["is_system_preset"] is False
    assert out["scope_id"] == str(SCOPE)
    assert out["source"] == "duplicated"
    # and it is writable, unlike its source
    await svc.update_asset(int(out["id"]), SCOPE, AssetUpdate(description="mine now"))
    with pytest.raises(AssetError) as ei:
        await svc.update_asset(int(preset["id"]), SCOPE, AssetUpdate(description="no"))
    assert ei.value.code == "system_preset_readonly"


@pytest.mark.asyncio
async def test_duplicate_of_an_asset_outside_the_scope_is_404(svc):
    src = await _character(svc, name="Someone Elses")
    svc.assets.rows[int(src["id"])]["scope_id"] = 999
    with pytest.raises(AssetError) as ei:
        await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert ei.value.status == 404 and ei.value.code == "asset_not_found"


@pytest.mark.asyncio
async def test_duplicate_name_collision_is_409_with_the_existing_id(svc):
    src = await _character(svc, name="Sang Yao")
    clash = await _character(svc, name="Sang Yao (copy)")
    with pytest.raises(AssetError) as ei:
        await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert ei.value.status == 409 and ei.value.code == "asset_exists"
    assert ei.value.extra["existing_asset_id"] == clash["id"]


@pytest.mark.asyncio
async def test_duplicate_copies_loadouts_default_first_and_adds_no_extra_default(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    new_id = int(out["id"])
    created = [lo for lo in svc.relations.loadouts.values() if lo["asset_id"] == new_id]
    assert [lo["name"] for lo in created] == ["Night raid", "Default"], (
        "the default must be created FIRST — uq_loadout_default is a partial "
        "unique index Postgres checks row by row and cannot defer"
    )
    assert [lo["is_default"] for lo in created] == [True, False]
    assert len(created) == len(src["loadouts"]), "no auto-Default on top of the copy"
    night = created[0]
    src_night = next(lo for lo in src["loadouts"] if lo["name"] == "Night raid")
    assert night["costume_ids"] == [int(c) for c in src_night["costume_ids"]]
    assert night["prompt_extra"] == src_night["prompt_extra"]
    assert night["sort_order"] == src_night["sort_order"]


@pytest.mark.asyncio
async def test_a_character_without_loadouts_still_gets_its_default(svc):
    """``create_asset`` guarantees every character has one; a preset seeded
    without loadouts must not produce a copy that breaks the invariant."""
    src = await _character(svc, name="Loadoutless")
    for lid in [
        lo["id"]
        for lo in svc.relations.loadouts.values()
        if lo["asset_id"] == int(src["id"])
    ]:
        del svc.relations.loadouts[lid]
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    created = [
        lo for lo in svc.relations.loadouts.values() if lo["asset_id"] == int(out["id"])
    ]
    assert [(lo["name"], lo["is_default"]) for lo in created] == [("Default", True)]


@pytest.mark.asyncio
async def test_duplicate_remaps_each_file_onto_the_new_loadout(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    new_id = int(out["id"])
    new_night = next(
        lo
        for lo in svc.relations.loadouts.values()
        if lo["asset_id"] == new_id and lo["name"] == "Night raid"
    )
    src_night = next(lo for lo in src["loadouts"] if lo["name"] == "Night raid")
    by_slot = {f["slot"]: f for f in out["files"]}
    assert set(by_slot) == {"sheet", "worn"}
    assert by_slot["sheet"]["loadout_id"] is None, "a null loadout stays null"
    assert by_slot["worn"]["loadout_id"] == str(new_night["id"])
    assert by_slot["worn"]["loadout_id"] != src_night["id"], (
        "copying the loadout_id verbatim points the copy's file at the SOURCE's "
        "loadout row"
    )
    assert by_slot["sheet"]["resource_id"] == IN_SCOPE_RESOURCE
    assert by_slot["worn"]["resource_id"] == R_WORN


@pytest.mark.asyncio
async def test_duplicate_skips_a_file_whose_resource_is_not_in_the_scope(svc):
    src = await _duplicable_character(svc)
    svc.relations.in_scope_resources -= {int(R_WORN)}
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert [f["slot"] for f in out["files"]] == ["sheet"]


@pytest.mark.asyncio
async def test_duplicate_copies_outgoing_links_only(svc):
    src = await _duplicable_character(svc)
    assert src["linked_by"], "the fixture must have an incoming link to prove this"
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert [(link["to_asset_id"], link["relation"]) for link in out["links"]] == [
        (link["to_asset_id"], link["relation"]) for link in src["links"]
    ]
    assert out["linked_by"] == [], (
        "an incoming link says someone ELSE points here; copying it would make "
        "the audio voice two characters"
    )


@pytest.mark.asyncio
async def test_duplicate_does_not_copy_project_refs(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert (int(src["id"]), 55) in svc.relations.refs
    assert [r for r in svc.relations.refs if r[0] == int(out["id"])] == []


@pytest.mark.asyncio
async def test_duplicate_keeps_an_in_scope_cover(svc):
    src = await _duplicable_character(svc)
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert out["cover_file_id"] == COVER


@pytest.mark.asyncio
async def test_duplicate_drops_a_cover_the_caller_cannot_see(svc):
    """The source may be a preset (or a row whose cover has since left the
    scope). Carrying the id over unchecked is a cross-tenant reference — the
    same 404 ``update_asset`` refuses, so here it is dropped rather than copied.
    """
    src = await _duplicable_character(svc)
    svc.relations.in_scope_resources -= {int(COVER)}
    out = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert out["cover_file_id"] is None


@pytest.mark.asyncio
async def test_the_whole_copy_happens_inside_one_unit_of_work(svc, monkeypatch):
    """A half-copied asset (header committed, files not) is worse than no copy:
    it is a row the user must find and delete by hand."""
    src = await _duplicable_character(svc)
    events: list[tuple] = []

    def _snapshot():
        return (
            len(svc.assets.rows),
            len(svc.relations.loadouts),
            len(svc.relations.files),
            len(svc.relations.links),
        )

    @asynccontextmanager
    async def _spy(enabled):
        events.append(("enter", _snapshot()))
        yield None
        events.append(("exit", _snapshot()))

    monkeypatch.setattr(assets_service, "maybe_unit_of_work", _spy)
    await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert [e[0] for e in events] == ["enter", "exit"], "exactly one transaction"
    before, after = events[0][1], events[1][1]
    assert all(
        a > b for a, b in zip(after, before)
    ), "the asset row AND every relation copy must land inside the transaction"
