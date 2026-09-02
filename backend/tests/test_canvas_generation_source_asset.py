"""A canvas run launched from an asset card must land in the COLUMN (P4 T7).

T5 stamped ``params.source_asset_id`` and stopped there. ``GET
/generated?source_asset_id=`` filters ``generated_media.source_asset_id`` — the
column — and so does the asset sheet's generation history, so the stamp had no
reader on the canvas path: the exact shape the retired ``entity_kind`` /
``entity_id`` stamp had before it, and the reason it was retired.

What this file pins:

* the column is written, from the params the canvas sent;
* the params copy SURVIVES (it is provenance a human reads on one row; the
  column is the index — they are not alternatives);
* a value that cannot be resolved to a real asset in the registration scope is
  dropped to ``None`` with a log, never raised. ``source_asset_id`` FK-refs
  ``assets.id``, so an unverified id would be an IntegrityError that loses a
  finished, billed generation over a provenance note;
* the daemon branch carries the same stamp on its ticket, resolved server-side.

Pure-process: the store, the scope resolver and the asset lookup are all
faked — no Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.workflows.canvas_generation as wf
from app.workflows.canvas_generation import persist_canvas_generation_step

SCOPE = 727145299382534111
ASSET = 700000000000000001
OTHER_ASSET = 700000000000000002


def _patch_assets(monkeypatch, visible=(ASSET,)):
    """Fake ``AssetsRepository.get`` — an asset is resolvable in ONE scope."""
    asked: list[tuple[int, int]] = []

    class _Repo:
        async def get(self, asset_id, scope_id):
            asked.append((int(asset_id), int(scope_id)))
            if int(asset_id) in visible and int(scope_id) == SCOPE:
                return {"id": int(asset_id)}
            return None

    import app.repositories.assets_repository as repo_mod

    monkeypatch.setattr(repo_mod, "AssetsRepository", _Repo)
    return asked


async def _persist(params, register=None):
    register = register or AsyncMock(return_value={"id": 55})
    with (
        patch("app.workflows.canvas_generation.register_generated_media", new=register),
        patch(
            "app.workflows.canvas_generation._registration_scope_id",
            new=AsyncMock(return_value=SCOPE),
        ),
    ):
        await persist_canvas_generation_step(
            media={
                "media_kind": "image",
                "local_path": None,
                "remote_url": "https://cdn/x.png",
                "provider": "volcengine",
                "model": "doubao-seedream",
            },
            user_id="u1",
            canvas_id=123,
            node_id="n1",
            prompt="a cat",
            params=params,
        )
    return register.await_args.kwargs["origin"]


@pytest.mark.asyncio
async def test_the_upstream_asset_reaches_the_column(monkeypatch):
    _patch_assets(monkeypatch)

    origin = await _persist({"source_asset_id": str(ASSET)})

    assert origin.source_asset_id == ASSET
    assert isinstance(origin.source_asset_id, int), "the column is a BIGINT"


@pytest.mark.asyncio
async def test_the_params_copy_survives_beside_the_column(monkeypatch):
    """Not either/or. The params copy is what a human reads on one row; the
    column is what the filter and the history panel index on."""
    _patch_assets(monkeypatch)

    origin = await _persist({"source_asset_id": str(ASSET), "loadout_id": "9"})

    assert origin.params["source_asset_id"] == str(ASSET)
    assert origin.params["loadout_id"] == "9"
    assert origin.source_asset_id == ASSET


@pytest.mark.asyncio
async def test_a_run_with_no_asset_card_writes_nothing(monkeypatch):
    asked = _patch_assets(monkeypatch)

    for params in ({}, {"source_asset_id": None}, {"source_asset_id": ""}):
        origin = await _persist(dict(params))
        assert origin.source_asset_id is None

    assert asked == [], "an absent stamp must not cost a lookup"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not-a-number", "12abc", [], {"a": 1}])
async def test_a_non_numeric_stamp_is_dropped_not_raised(monkeypatch, raw):
    """A client bug must not lose a finished generation."""
    _patch_assets(monkeypatch)

    origin = await _persist({"source_asset_id": raw})

    assert origin.source_asset_id is None


@pytest.mark.asyncio
async def test_an_id_that_resolves_to_no_asset_is_dropped(monkeypatch):
    """The column FK-references ``assets.id``: an id that does not exist would
    make the INSERT raise, and route C turns that into a failed run — the
    picture is gone over a provenance note."""
    asked = _patch_assets(monkeypatch)

    origin = await _persist({"source_asset_id": str(OTHER_ASSET)})

    assert origin.source_asset_id is None
    assert asked == [(OTHER_ASSET, SCOPE)], "it was checked, not assumed"


@pytest.mark.asyncio
async def test_the_asset_is_verified_in_the_REGISTRATION_scope(monkeypatch):
    """Not the runner's personal team, and not a scope the client named: the
    same scope the row is being filed into, so a card pointing at another
    tenant's asset cannot stamp it."""
    asked = _patch_assets(monkeypatch)

    await _persist({"source_asset_id": str(ASSET)})

    assert asked == [(ASSET, SCOPE)]


@pytest.mark.asyncio
async def test_a_lookup_that_blows_up_drops_the_stamp_rather_than_the_run(
    monkeypatch,
):
    class _Broken:
        async def get(self, asset_id, scope_id):
            raise RuntimeError("db down")

    import app.repositories.assets_repository as repo_mod

    monkeypatch.setattr(repo_mod, "AssetsRepository", _Broken)

    origin = await _persist({"source_asset_id": str(ASSET)})

    assert origin.source_asset_id is None


@pytest.mark.asyncio
async def test_the_helper_answers_none_for_an_unresolvable_scope(monkeypatch):
    """``_source_asset_id_for`` is the one place this decision is made, so it
    is worth asking directly as well as through the step."""
    _patch_assets(monkeypatch)

    assert (
        await wf._source_asset_id_for({"source_asset_id": str(ASSET)}, SCOPE) == ASSET
    )
    assert await wf._source_asset_id_for({"source_asset_id": str(ASSET)}, 999) is None
    assert await wf._source_asset_id_for(None, SCOPE) is None
