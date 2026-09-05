"""Explicit library membership (``assets.in_library``, mig 449) at the SERVICE
level, on the same in-memory fakes as the rest of this suite.

THE RULING UNDER TEST. An asset is in the 资产库 only when somebody deliberately
put it there. So each write path has to be classified once, and this file is
where the classification is pinned:

* deliberate → ``in_library=True``: ``create_asset`` (``POST /assets`` and the
  Generated inbox's Save-as-Asset), ``duplicate``.
* a side effect of project work → ``in_library=False``: ``_import_one``
  (一键导入). The project-entity migration workflow's ``_apply`` used to be the
  same class, pinned in ``tests/db/test_assets_migration_integration.py``; both
  were deleted with mig 451's DROP of the two ``_legacy_*`` tables, so
  ``_import_one`` is now the only member of that class.

Two of these are worth spelling out because the obvious implementation gets
them backwards:

* ``duplicate`` makes the COPY a member whatever the source was. Copying the
  source's membership would make "duplicate this imported character so I can
  edit it" produce a row the shelf still refuses to show.
* the import's ADOPTION branch (the name already exists as an asset) must not
  touch membership at all. That row may be a hand-made asset the user already
  put in their library, and a 一键导入 re-run evicting it would be the import
  silently undoing a user's decision.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.assets import AssetCreate, AssetUpdate, DuplicateRequest
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from tests.services.assets.test_assets_service import (
    SCOPE,
    USER,
    FakeAssetsRepo,
    FakeRelationsRepo,
)


@pytest.fixture
def svc():
    return AssetsService(
        assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo()
    )


async def _character(svc, name="Sang Yao", **kw):
    return await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name=name), USER, **kw
    )


def _stub_entities(monkeypatch, *, characters=(), locations=()):
    async def _names(project_id):
        return {"character": list(characters), "location": list(locations)}

    monkeypatch.setattr(assets_service, "_script_entity_names", _names)


# ── who lands in the library, and who does not ─────────────────────────────


@pytest.mark.asyncio
async def test_a_hand_made_asset_is_in_the_library(svc):
    """``POST /assets`` is the deliberate act itself — there is nothing to
    confirm afterwards."""
    assert (await _character(svc))["in_library"] is True


@pytest.mark.asyncio
async def test_the_save_as_asset_path_is_in_the_library(svc):
    """``generated_inbox_service`` creates through the SAME method with
    ``source='generated'`` and no ``in_library=`` argument, so the default is
    what decides it. Picking a generation out of the inbox and naming an asset
    for it is a deliberate act; this pins that the default agrees."""
    row = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type="character", name="Sang Yao", source="generated"),
        USER,
    )
    assert row["source"] == "generated"
    assert row["in_library"] is True


@pytest.mark.asyncio
async def test_a_duplicate_is_in_the_library_even_when_its_source_is_not(svc):
    """The copy's membership is decided by the ACT of duplicating, not
    inherited. Inheriting it would produce a copy the shelf cannot show — a
    duplicate the user has no way to find."""
    src = await _character(svc, in_library=False)
    assert src["in_library"] is False

    copy = await svc.duplicate(
        int(src["id"]), SCOPE, USER, DuplicateRequest(name="Sang Yao (copy)")
    )
    assert copy["in_library"] is True
    # …and duplicating did not drag the SOURCE into the library with it.
    assert svc.assets.rows[int(src["id"])]["in_library"] is False


@pytest.mark.asyncio
async def test_import_from_script_lands_outside_the_library(svc, monkeypatch):
    _stub_entities(monkeypatch, characters=["Sang Yao"], locations=["Rooftop"])
    out = await svc.import_from_script(SCOPE, 55, USER)

    assert out["created"] == 2
    ids = [int(i["asset_id"]) for i in out["items"]]
    assert [svc.assets.rows[i]["in_library"] for i in ids] == [False, False]
    # The rows are real and complete — this is "not in the library", not
    # "not created". Their project refs landed exactly as before.
    assert [svc.assets.rows[i]["source"] for i in ids] == [
        "script_import",
        "script_import",
    ]
    assert len(svc.relations.refs) == 2


@pytest.mark.asyncio
async def test_import_does_not_evict_an_asset_the_user_already_adopted(
    svc, monkeypatch
):
    """The adoption branch: the name already exists, so the import's only job
    is the missing project ref. Flipping membership here would let 一键导入
    silently undo the user's own Add To Library."""
    existing = await _character(svc, name="Sang Yao")
    assert existing["in_library"] is True

    _stub_entities(monkeypatch, characters=["Sang Yao"])
    out = await svc.import_from_script(SCOPE, 55, USER)

    assert out["created"] == 0 and out["linked"] == 1
    assert svc.assets.rows[int(existing["id"])]["in_library"] is True


# ── the add / remove action ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_and_remove_move_the_flag_and_answer_the_row(svc):
    a = await _character(svc, in_library=False)

    added = await svc.set_library_membership(int(a["id"]), SCOPE, in_library=True)
    assert added["in_library"] is True
    assert added["id"] == a["id"], "the caller re-renders THIS card"

    removed = await svc.set_library_membership(int(a["id"]), SCOPE, in_library=False)
    assert removed["in_library"] is False
    assert svc.assets.rows[int(a["id"])]["in_library"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, False])
async def test_setting_the_state_it_already_has_is_a_no_op_not_a_refusal(svc, value):
    """Idempotent on BOTH sides. "It is already in your library" is the outcome
    the user asked for; a 409 would make a double-clicked Add To Library button
    report a failure for work that is done."""
    a = await _character(svc, in_library=value)
    out = await svc.set_library_membership(int(a["id"]), SCOPE, in_library=value)
    assert out["in_library"] is value


@pytest.mark.asyncio
async def test_removing_from_the_library_keeps_everything_else(svc):
    """The removal is NOT a delete and NOT an unlink — the row, its project
    refs and its loadouts all survive. Only the shelf stops counting it."""
    a = await _character(svc)
    await svc.link_project(int(a["id"]), SCOPE, 55, USER)
    before_refs = list(svc.relations.refs)

    await svc.set_library_membership(int(a["id"]), SCOPE, in_library=False)

    row = svc.assets.rows[int(a["id"])]
    assert row["deleted_at"] is None
    assert row["name"] == "Sang Yao"
    assert list(svc.relations.refs) == before_refs


@pytest.mark.asyncio
async def test_a_system_preset_refuses_the_toggle(svc):
    """``_require_writable``, not ``_require``: a preset is a GLOBAL row
    readable from every scope, so one team flipping its membership would move
    it for everybody."""
    svc.assets.rows[7] = svc.assets._row(
        id=7,
        scope_id=None,
        asset_type="character",
        name="Preset",
        is_system_preset=True,
    )
    with pytest.raises(AssetError) as e:
        await svc.set_library_membership(7, SCOPE, in_library=False)
    assert e.value.status == 403
    assert e.value.code == "system_preset_readonly"


@pytest.mark.asyncio
async def test_an_unknown_asset_is_a_typed_404(svc):
    with pytest.raises(AssetError) as e:
        await svc.set_library_membership(999999, SCOPE, in_library=True)
    assert e.value.status == 404 and e.value.code == "asset_not_found"


# ── there is exactly ONE write path, and it stays that way ─────────────────


def test_the_patch_surface_cannot_reach_the_column():
    """``AssetUpdate`` does not declare ``in_library``, and ``extra="forbid"``
    turns an attempt into a TYPED refusal rather than a silent drop.

    This is what makes "one write path" a fact rather than a claim. With the
    field declared, PATCH and the two routes would converge only at the
    repository, so anything later added to ``set_library_membership`` — an
    audit row, a refusal, a side effect — would be bypassed by the PATCH path
    with nothing going red.
    """
    assert "in_library" not in AssetUpdate.model_fields

    with pytest.raises(ValidationError) as e:
        AssetUpdate(name="Sang Yao", in_library=False)
    # Names the offending key, so a client that guessed wrong is told where the
    # real action is instead of getting a 200 that changed nothing.
    assert "in_library" in str(e.value)


def test_set_library_membership_is_the_only_writer_of_the_column():
    """The divergence guard.

    Reads the service's own source and asserts every write of ``in_library``
    lives in a method that is allowed to make one. A second write path added
    later — a new service helper, a field slipped back into a PATCH dump —
    fails here even if its behaviour happens to match today, which is the point:
    the invariant the router's comment states is checked, not trusted.

    The four permitted sites are the creation defaults (``create_asset``
    stamping the keyword argument, ``duplicate`` deciding the copy's own value,
    ``_import_one`` passing False) and ``set_library_membership`` itself. Each
    is a place where membership is DECIDED at birth or by the one named action —
    none of them is a general-purpose column write.
    """
    source = Path(assets_service.__file__).read_text()
    tree = ast.parse(source)

    writers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, node) or ""
        # A write is the column name as a dict key or as a keyword argument;
        # a bare mention inside a docstring or comment is not.
        stripped = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        if '"in_library"' in stripped or "in_library=" in stripped:
            writers.add(node.name)

    assert writers == {
        "create_asset",
        "duplicate",
        "_import_one",
        "set_library_membership",
    }, f"a new writer of in_library appeared: {sorted(writers)}"


# ── the read side delegates the filter ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_assets_hands_the_library_filter_to_the_repository(svc):
    """``library`` is a SQL predicate, unlike ``readiness`` (derived per row and
    applied in Python). It must reach the repo, not be re-implemented here."""
    await svc.list_assets(SCOPE, library="out", sort="name")
    assert svc.assets.list_calls[-1]["library"] == "out"

    await svc.list_assets(SCOPE, library="all")
    assert svc.assets.list_calls[-1]["library"] == "all"
