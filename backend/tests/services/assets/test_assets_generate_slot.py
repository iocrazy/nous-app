"""generate-slot on the asset shelf (P2 Task 4) — service level, no DB/network.

Two external calls are faked and nothing else: the image provider chain
(``ImageGenerationService``) and the Tier-1 ingest
(``register_generated_media``). Everything between them — slot validation,
loadout ownership, linked-prompt resolution, reference ordering, the
per-unit failure ledger — runs for real.

The behaviours with a silent failure mode, pinned below:

* **per-unit failures are REPORTED, never swallowed.** ``count=3`` with one
  provider error answers 2 ids AND one ``failed`` entry naming the index. A
  run that quietly returns 2 of 3 is indistinguishable from one the user
  asked 2 of — and they paid for 3.
* **all-fail is a 503, not an empty 202.** ``{"generation_ids": []}`` with a
  200 reads as "done" in every client that does not inspect list length.
* **the loadout is a filter, not decoration** — its ``prompt_extra`` and its
  costumes/props are what make a generation match the outfit the user picked.
  Silently generating the un-dressed asset would be a paid wrong answer.
* **the reference cap is a provider limit** (3), and the primary slot must
  never be the reference that got dropped.
* **presets stay read-only** — this path inserts rows keyed to the asset.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.schemas.assets import (
    AssetCreate,
    AttachFileRequest,
    GenerateSlotRequest,
    LinkRequest,
    LoadoutCreate,
)
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from tests.services.assets.test_assets_service import (
    SCOPE,
    USER,
    FakeAssetsRepo,
    FakeRelationsRepo,
)

SHEET_FILE = "727145299382534146"
SECOND_FILE = "727145299382534147"
THIRD_FILE = "727145299382534148"
FOURTH_FILE = "727145299382534149"


class FakeImageGen:
    """Stands in for ``ImageGenerationService`` — the paid call.

    ``results`` is consumed one entry per unit: a dict is returned, an
    Exception instance is raised. That is how "unit 1 of 3 failed" is set up
    without any timing games.
    """

    def __init__(self, results=None):
        self.calls: list[dict] = []
        self._results = list(results or [])

    def __call__(self):  # the service does ImageGenerationService()
        return self

    async def generate_image(self, **kw):
        self.calls.append(dict(kw))
        if not self._results:
            return {"image_url": f"https://cdn.test/{len(self.calls)}.png"}
        nxt = self._results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeRegister:
    def __init__(self, raises=None):
        self.calls: list[dict] = []
        self._raises = raises
        self._next = 900

    async def __call__(self, **kw):
        self.calls.append(dict(kw))
        if self._raises is not None:
            raise self._raises
        self._next += 1
        return {"id": self._next}


class FakeGeneratedRepo:
    def __init__(self, missing=False):
        self.stamped: list[tuple] = []
        self._missing = missing

    async def set_source_asset(self, gen_id, asset_id, *, scope_id=None):
        self.stamped.append((int(gen_id), int(asset_id), scope_id))
        return None if self._missing else {"id": str(gen_id)}


@pytest.fixture
def gen_repo():
    return FakeGeneratedRepo()


@pytest.fixture
def svc(gen_repo):
    return AssetsService(
        assets_repo=FakeAssetsRepo(),
        relations_repo=FakeRelationsRepo(),
        generated_repo=gen_repo,
    )


@pytest.fixture(autouse=True)
def materialized(monkeypatch, tmp_path):
    """Fake ``media_storage.materialize`` — the ONE filesystem/object-store
    touch on this path. Returns the list of stored paths it was asked for, so
    a test can assert WHICH file was chosen by the ladder. Real temp files, so
    the caller's ``os.path.isfile`` guard runs for real."""
    asked: list[str] = []

    @asynccontextmanager
    async def _fake(file_path):
        asked.append(str(file_path))
        dest = tmp_path / str(file_path).replace("/", "_")
        dest.write_bytes(b"png-bytes")
        yield dest

    monkeypatch.setattr(assets_service, "materialize", _fake)
    return asked


@pytest.fixture
def imagegen(monkeypatch):
    fake = FakeImageGen()
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    return fake


@pytest.fixture
def register(monkeypatch):
    fake = FakeRegister()
    monkeypatch.setattr(assets_service, "register_generated_media", fake)
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


def _req(**kw):
    kw.setdefault("slot", "sheet")
    return GenerateSlotRequest(**kw)


# ── preview ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_composes_the_prompt_and_the_reference_list(svc):
    a = await _asset(svc, prompt_positive="a swordswoman", prompt_negative="glasses")
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", None)
    assert "a swordswoman" in out["positive"]
    assert "character sheet" in out["positive"]
    assert "glasses" in out["negative"]
    assert out["reference_resource_ids"] == [SHEET_FILE]
    assert out["model"] is None


@pytest.mark.asyncio
async def test_preview_is_allowed_on_a_system_preset(svc):
    """Reading what WOULD be sent writes nothing; the 403 belongs on generate."""
    a = await _asset(svc)
    svc.assets.make_preset(a["id"])
    out = await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", None)
    assert out["positive"]


@pytest.mark.asyncio
async def test_invalid_slot_is_422(svc):
    a = await _asset(svc, asset_type="prop", name="Jade Seal")
    with pytest.raises(AssetError) as e:
        await svc.preview_generate_slot(int(a["id"]), SCOPE, "expressions", None)
    assert (e.value.status, e.value.code) == (422, "invalid_slot")


@pytest.mark.asyncio
async def test_audio_slot_is_slot_not_generatable_not_invalid(svc):
    """``primary`` IS a valid audio slot — the refusal must say WHY (there is
    nothing to draw), not "no such slot", or the user goes looking for a typo."""
    a = await _asset(svc, asset_type="audio", name="Rain Loop", subtype="sfx")
    with pytest.raises(AssetError) as e:
        await svc.preview_generate_slot(int(a["id"]), SCOPE, "primary", None)
    assert (e.value.status, e.value.code) == (422, "slot_not_generatable")


@pytest.mark.asyncio
async def test_a_foreign_loadout_is_422(svc):
    a = await _asset(svc)
    other = await _asset(svc, name="Lin Xue")
    foreign = (await svc.relations.list_loadouts(int(other["id"])))[0]
    with pytest.raises(AssetError) as e:
        await svc.preview_generate_slot(
            int(a["id"]), SCOPE, "sheet", str(foreign["id"])
        )
    assert (e.value.status, e.value.code) == (422, "loadout_mismatch")


@pytest.mark.asyncio
async def test_unknown_asset_is_404(svc):
    with pytest.raises(AssetError) as e:
        await svc.preview_generate_slot(999999, SCOPE, "sheet", None)
    assert e.value.status == 404


@pytest.mark.asyncio
async def test_the_loadout_contributes_its_prompt_and_its_costume(svc):
    a = await _asset(svc)
    costume = await _asset(svc, asset_type="costume", name="Night Raid Cloak")
    costume = await svc.assets.update(
        int(costume["id"]), SCOPE, {"prompt_positive": "a dark hooded cloak"}
    )
    await svc.add_link(
        int(a["id"]),
        SCOPE,
        LinkRequest(to_asset_id=str(costume["id"]), relation="wears"),
    )
    lo = await svc.create_loadout(
        int(a["id"]),
        SCOPE,
        LoadoutCreate(
            name="Night raid",
            costume_ids=[str(costume["id"])],
            prompt_extra="moonlit rooftop",
        ),
    )
    out = await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", str(lo["id"]))
    p = out["positive"]
    assert "moonlit rooftop" in p
    assert "a dark hooded cloak" in p
    assert p.index("moonlit rooftop") < p.index("a dark hooded cloak")
    assert p.index("a dark hooded cloak") < p.index("character sheet")


@pytest.mark.asyncio
async def test_without_a_loadout_every_linked_costume_and_prop_contributes(svc):
    a = await _asset(svc)
    costume = await _asset(svc, asset_type="costume", name="Cloak")
    await svc.assets.update(
        int(costume["id"]), SCOPE, {"prompt_positive": "a dark hooded cloak"}
    )
    prop = await _asset(svc, asset_type="prop", name="Jade Seal")
    await svc.assets.update(
        int(prop["id"]), SCOPE, {"prompt_positive": "a carved jade seal"}
    )
    await svc.add_link(
        int(a["id"]),
        SCOPE,
        LinkRequest(to_asset_id=str(costume["id"]), relation="wears"),
    )
    await svc.add_link(
        int(a["id"]), SCOPE, LinkRequest(to_asset_id=str(prop["id"]), relation="holds")
    )
    p = (await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", None))[
        "positive"
    ]
    assert "a dark hooded cloak" in p
    assert "a carved jade seal" in p
    # wears before holds — deterministic, so two identical requests compose
    # the same prompt and hit the same provider-side cache.
    assert p.index("a dark hooded cloak") < p.index("a carved jade seal")


@pytest.mark.asyncio
async def test_an_empty_loadout_excludes_a_linked_costume(svc):
    """The loadout is a FILTER. A costume linked to the character but left out
    of the picked outfit must not leak into the prompt."""
    a = await _asset(svc)
    costume = await _asset(svc, asset_type="costume", name="Cloak")
    await svc.assets.update(
        int(costume["id"]), SCOPE, {"prompt_positive": "a dark hooded cloak"}
    )
    await svc.add_link(
        int(a["id"]),
        SCOPE,
        LinkRequest(to_asset_id=str(costume["id"]), relation="wears"),
    )
    lo = (await svc.relations.list_loadouts(int(a["id"])))[0]
    p = (await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", str(lo["id"])))[
        "positive"
    ]
    assert "a dark hooded cloak" not in p


@pytest.mark.asyncio
async def test_references_are_capped_at_three_in_priority_order(svc):
    a = await _asset(svc)
    await _attach(svc, a["id"], FOURTH_FILE, "extras")
    await _attach(svc, a["id"], THIRD_FILE, "stills")
    await _attach(svc, a["id"], SECOND_FILE, "worn")
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", None)
    assert out["reference_resource_ids"] == [SHEET_FILE, SECOND_FILE, THIRD_FILE]


# ── generate ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_registers_each_unit_and_stamps_the_source_asset(
    svc, imagegen, register, gen_repo
):
    a = await _asset(svc, prompt_positive="a swordswoman")
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=2))

    assert out["inbox_state"] == "unreviewed"
    assert out["failed"] == []
    assert len(out["generation_ids"]) == 2
    assert gen_repo.stamped == [
        (int(g), int(a["id"]), SCOPE) for g in out["generation_ids"]
    ]

    call = imagegen.calls[0]
    assert call["project_id"] == "asset"
    assert call["node_id"] == f"asset:{a['id']}:sheet"
    assert "a swordswoman" in call["prompt"]
    assert call["provider_name"] is None
    assert call["user_id"] == USER
    assert call["reference_image_url"].endswith(f"/api/v1/resources/{SHEET_FILE}/cover")

    reg = register.calls[0]
    assert reg["scope_id"] == SCOPE
    assert reg["user_id"] == USER
    assert reg["mime"] == "image/png"
    origin = reg["origin"]
    assert origin.kind == "agent_run"
    assert origin.node_id == f"asset:{a['id']}:sheet"
    assert origin.params["target_slot"] == "sheet"
    assert origin.params["loadout_id"] is None
    assert origin.params["negative"]


@pytest.mark.asyncio
async def test_no_reference_files_means_no_reference_url(svc, imagegen, register):
    a = await _asset(svc)
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert imagegen.calls[0]["reference_image_url"] is None


@pytest.mark.asyncio
async def test_one_provider_failure_out_of_three_is_reported_not_swallowed(
    svc, monkeypatch, register
):
    fake = FakeImageGen(
        [
            {"image_url": "https://cdn.test/1.png"},
            RuntimeError("provider 503"),
            {"image_url": "https://cdn.test/3.png"},
        ]
    )
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    a = await _asset(svc)
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=3))
    assert len(out["generation_ids"]) == 2
    assert len(out["failed"]) == 1
    assert out["failed"][0]["index"] == 1
    assert out["failed"][0]["code"] == "generation_failed"
    assert "provider 503" in out["failed"][0]["detail"]


@pytest.mark.asyncio
async def test_every_unit_failing_is_a_503_carrying_the_providers_message(
    svc, monkeypatch, register
):
    fake = FakeImageGen([RuntimeError("no image model configured")] * 2)
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=2))
    assert (e.value.status, e.value.code) == (503, "generation_failed")
    assert "no image model configured" in e.value.detail
    assert len(e.value.extra["failed"]) == 2


@pytest.mark.asyncio
async def test_a_provider_that_returns_neither_url_nor_file_is_a_failure(
    svc, monkeypatch, register
):
    """``{}`` back from the provider used to become ``source_url=None`` and a
    ValueError inside the ingest — a defect of ours reported as an ingest
    error. It is a generation failure, and it says so."""
    fake = FakeImageGen([{"image_url": "", "image_path": None}])
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=1))
    assert e.value.extra["failed"][0]["code"] == "generation_failed"


@pytest.mark.asyncio
async def test_a_local_file_provider_result_routes_to_source_path(
    svc, monkeypatch, register
):
    """jimeng-cli / codex write a FILE and return no url. Passing that as
    ``source_url`` is a ValueError in the ingest, i.e. every local-provider
    generation would land in ``failed`` with an ingest error."""
    fake = FakeImageGen([{"image_url": "", "image_path": "/tmp/x/out.png"}])
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    a = await _asset(svc)
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=1))
    assert register.calls[0]["source_path"] == "/tmp/x/out.png"
    assert register.calls[0]["source_url"] is None


@pytest.mark.asyncio
async def test_an_ingest_failure_gets_its_own_code(svc, imagegen, monkeypatch):
    """ "the provider never answered" and "we could not store what it answered"
    need different next steps, so they must not share a code."""
    monkeypatch.setattr(
        assets_service, "register_generated_media", FakeRegister(raises=OSError("disk"))
    )
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=1))
    assert e.value.extra["failed"][0]["code"] == "register_failed"


@pytest.mark.asyncio
async def test_a_stamp_that_matches_nothing_is_a_reported_failure(
    svc, imagegen, register, monkeypatch
):
    """A generation the Assets tab can never find is worse than none: it is a
    row the user paid for that shows up nowhere they were looking."""
    svc.generated = FakeGeneratedRepo(missing=True)
    a = await _asset(svc)
    with pytest.raises(AssetError) as e:
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=1))
    assert e.value.extra["failed"][0]["code"] == "register_failed"


@pytest.mark.asyncio
async def test_an_explicit_model_reaches_the_provider(svc, imagegen, register):
    a = await _asset(svc)
    await svc.generate_slot(
        int(a["id"]), SCOPE, USER, _req(model="doubao-seedream-4-0")
    )
    assert imagegen.calls[0]["model"] == "doubao-seedream-4-0"
    assert register.calls[0]["origin"].model == "doubao-seedream-4-0"


@pytest.mark.asyncio
async def test_no_model_falls_through_to_the_catalog_default(svc, imagegen, register):
    a = await _asset(svc)
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert imagegen.calls[0]["model"] == assets_service.DEFAULT_IMAGE_MODEL


@pytest.mark.asyncio
async def test_the_model_recorded_is_the_one_that_actually_ran(
    svc, monkeypatch, register
):
    """The catalog resolves the ``dall-e-3`` sentinel to whatever the admin
    enabled. Filing the sentinel would name a model that never ran — the same
    lie the preview refuses to tell."""
    fake = FakeImageGen(
        [{"image_url": "https://cdn.test/1.png", "model": "doubao-seedream-4-0-250828"}]
    )
    monkeypatch.setattr(assets_service, "ImageGenerationService", fake)
    a = await _asset(svc)
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert register.calls[0]["origin"].model == "doubao-seedream-4-0-250828"


@pytest.mark.asyncio
async def test_a_system_preset_refuses_to_generate(svc, imagegen, register):
    a = await _asset(svc)
    svc.assets.make_preset(a["id"])
    with pytest.raises(AssetError) as e:
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert (e.value.status, e.value.code) == (403, "system_preset_readonly")
    assert imagegen.calls == [], "a preset must not reach the paid call"


@pytest.mark.asyncio
async def test_a_refused_slot_never_reaches_the_provider(svc, imagegen, register):
    a = await _asset(svc, asset_type="audio", name="Rain", subtype="sfx")
    with pytest.raises(AssetError):
        await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(slot="primary"))
    assert imagegen.calls == []


@pytest.mark.asyncio
async def test_a_successful_run_bumps_the_assets_clock(svc, imagegen, register):
    a = await _asset(svc)
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert int(a["id"]) in svc.relations.touched


@pytest.mark.asyncio
async def test_the_loadout_id_is_recorded_on_every_generation(svc, imagegen, register):
    a = await _asset(svc)
    lo = (await svc.relations.list_loadouts(int(a["id"])))[0]
    await svc.generate_slot(
        int(a["id"]), SCOPE, USER, _req(loadout_id=str(lo["id"]), count=2)
    )
    for call in register.calls:
        assert call["origin"].params["loadout_id"] == str(lo["id"])


# ── references actually reach the provider (fix round 1, C1) ───────────────


@pytest.mark.asyncio
async def test_reference_files_are_materialized_and_sent_as_local_paths(
    svc, imagegen, register, materialized
):
    """The url channel is vestigial: ark ignores it, jimeng never reads it,
    codex takes it only when it names a local file. What has to arrive is
    ``reference_image_paths``."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())

    assert materialized == [f"library/{SHEET_FILE}/original.png"]
    paths = imagegen.calls[0]["reference_image_paths"]
    assert paths and len(paths) == 1
    assert paths[0].endswith("original.png")
    assert out["skipped_references"] == []
    # …and the remote url still rides along for a future URL-based adapter.
    assert imagegen.calls[0]["reference_image_url"].endswith(
        f"/api/v1/resources/{SHEET_FILE}/cover"
    )


@pytest.mark.asyncio
async def test_a_reference_that_left_the_scope_is_skipped_not_sent(
    svc, imagegen, register, materialized
):
    """attach_file scope-checked this resource ONCE, at attach time.

    ``delete_resource_item`` later drops the ``resource_items`` row (and the
    trigger trashes the resource) while ``asset_files`` keeps its own — its FK
    is on ``resources.id``, not on ``resource_items``. The row therefore still
    EXISTS, which is why the fake keeps answering ``resource_media_rows`` for
    it: without the service's own re-check the bytes would go to ark / codex /
    jimeng and the derived image would land in the caller's inbox.

    ``regenerate_prompt`` already refuses the same file with a 404
    ``resource_not_found``. Two P2 paths, one question, one answer.
    """
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")

    # It leaves the scope. The ROW survives — that is the whole point.
    svc.relations.in_scope_resources.discard(int(SHEET_FILE))
    svc.relations.descoped_resources.add(int(SHEET_FILE))

    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())

    assert out["skipped_references"] == [
        {"resource_id": SHEET_FILE, "reason": "resource_not_found"}
    ]
    # Nothing about it reached the provider — not the bytes…
    assert materialized == []
    assert imagegen.calls[0]["reference_image_paths"] is None
    # …and not the unauthenticated /cover url that names it either.
    assert imagegen.calls[0]["reference_image_url"] is None
    # The run itself still succeeds: a missing reference is a worse picture,
    # not a broken request (same contract as an unmaterializable one).
    assert len(out["generation_ids"]) == 1


@pytest.mark.asyncio
async def test_the_scope_recheck_asks_about_every_reference_in_the_caller_scope(
    svc, imagegen, register
):
    """The guard is per-reference and asks with the REQUEST's scope.

    A check that ran once for the first id, or asked with the asset's own
    scope_id, would let a second stale reference through — and there is no
    signal anywhere when that happens.
    """
    a = await _asset(svc)
    for rid in (SHEET_FILE, SECOND_FILE):
        await _attach(svc, a["id"], rid, "expressions")
    svc.relations.scope_checks.clear()

    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(slot="expressions"))

    asked = [
        c
        for c in svc.relations.scope_checks
        if c[0] in (int(SHEET_FILE), int(SECOND_FILE))
    ]
    assert sorted(asked) == sorted(
        [(int(SHEET_FILE), int(SCOPE)), (int(SECOND_FILE), int(SCOPE))]
    )


@pytest.mark.asyncio
async def test_an_in_scope_reference_is_still_sent(
    svc, imagegen, register, materialized
):
    """The positive control for the guard: it refuses stale references, not
    every reference. Without this, deleting the whole materialize step would
    pass the test above."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())

    assert out["skipped_references"] == []
    assert materialized == [f"library/{SHEET_FILE}/original.png"]
    assert imagegen.calls[0]["reference_image_paths"]


@pytest.mark.asyncio
async def test_the_same_local_paths_serve_every_unit_of_the_run(
    svc, imagegen, register, materialized
):
    """One AsyncExitStack around the whole loop: materializing per unit would
    delete the temp file before the next call could read it."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(count=3))
    assert len(materialized) == 1, "materialized once, not once per unit"
    assert len({tuple(c["reference_image_paths"]) for c in imagegen.calls}) == 1


@pytest.mark.asyncio
async def test_an_unmaterializable_reference_is_dropped_and_REPORTED(
    svc, imagegen, register, monkeypatch
):
    """ "选了也生成了但图里没有" — the recorded failure this list prevents."""

    @asynccontextmanager
    async def _boom(file_path):
        raise OSError("object store unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(assets_service, "materialize", _boom)
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    # The run still succeeds — a worse picture is not a broken request.
    assert len(out["generation_ids"]) == 1
    assert out["skipped_references"] == [
        {
            "resource_id": SHEET_FILE,
            "reason": "materialize_failed: object store unreachable",
        }
    ]
    assert imagegen.calls[0]["reference_image_paths"] is None


@pytest.mark.asyncio
async def test_a_resource_with_no_image_file_is_reported_not_silently_dropped(
    svc, imagegen, register
):
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    svc.relations.resource_media[int(SHEET_FILE)] = {
        "file_path": "library/clip.mp4",
        "mime_type": "video/mp4",
        "thumbnail_path": None,
        "cover_image_path": None,
    }
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert out["skipped_references"] == [
        {"resource_id": SHEET_FILE, "reason": "no_image_file"}
    ]


@pytest.mark.asyncio
async def test_a_video_backed_reference_falls_back_to_its_thumbnail(
    svc, imagegen, register, materialized
):
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    svc.relations.resource_media[int(SHEET_FILE)] = {
        "file_path": "library/clip.mp4",
        "mime_type": "video/mp4",
        "thumbnail_path": "sb://library/derived/1/thumb.jpg",
        "cover_image_path": None,
    }
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert materialized == ["sb://library/derived/1/thumb.jpg"]


@pytest.mark.asyncio
async def test_an_http_stored_value_is_never_handed_to_materialize(
    svc, imagegen, register, materialized
):
    """An ``http`` column value is somebody else's URL, not a path of ours."""
    a = await _asset(svc)
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    svc.relations.resource_media[int(SHEET_FILE)] = {
        "file_path": "https://cdn.example/x.png",
        "mime_type": "image/png",
        "thumbnail_path": None,
        "cover_image_path": None,
    }
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert materialized == []
    assert out["skipped_references"][0]["reason"] == "no_image_file"
    # The wave's disclosed behaviour change: a run whose ONLY reference was
    # skipped must not name that resource in the (unauthenticated) cover URL
    # either — reverting `sent` back to `refs` turns this line red.
    assert imagegen.calls[0].get("reference_image_url") is None


@pytest.mark.asyncio
async def test_no_references_sends_no_paths_and_reports_no_skips(
    svc, imagegen, register
):
    a = await _asset(svc)
    out = await svc.generate_slot(int(a["id"]), SCOPE, USER, _req())
    assert imagegen.calls[0]["reference_image_paths"] is None
    assert imagegen.calls[0]["reference_image_url"] is None
    assert out["skipped_references"] == []


# ── loadout-scoped files (fix round 1, I3) ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_file_pinned_to_another_loadout_is_not_referenced(svc):
    """The prompt already excludes loadout Y's costume; a reference pointing
    at it would make one plan describe two outfits."""
    a = await _asset(svc)
    costume = await _asset(svc, asset_type="costume", name="Cloak")
    await svc.add_link(
        int(a["id"]),
        SCOPE,
        LinkRequest(to_asset_id=str(costume["id"]), relation="wears"),
    )
    lo_x = await svc.create_loadout(
        int(a["id"]), SCOPE, LoadoutCreate(name="X", costume_ids=[str(costume["id"])])
    )
    lo_y = await svc.create_loadout(
        int(a["id"]), SCOPE, LoadoutCreate(name="Y", costume_ids=[str(costume["id"])])
    )
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], SECOND_FILE, "worn", loadout_id=str(lo_x["id"]))
    await _attach(svc, a["id"], THIRD_FILE, "worn", loadout_id=str(lo_y["id"]))

    for_x = await svc.preview_generate_slot(
        int(a["id"]), SCOPE, "sheet", str(lo_x["id"])
    )
    assert for_x["reference_resource_ids"] == [SHEET_FILE, SECOND_FILE]


@pytest.mark.asyncio
async def test_without_a_loadout_only_unpinned_files_are_referenced(svc):
    a = await _asset(svc)
    lo = (await svc.relations.list_loadouts(int(a["id"])))[0]
    await _attach(svc, a["id"], SHEET_FILE, "sheet")
    await _attach(svc, a["id"], SECOND_FILE, "worn", loadout_id=str(lo["id"]))
    out = await svc.preview_generate_slot(int(a["id"]), SCOPE, "sheet", None)
    assert out["reference_resource_ids"] == [SHEET_FILE]


# ── aspect ratio (fix round 1, M6) ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset_type", "name", "slot", "expected"),
    [
        ("character", "Sang Yao", "sheet", "16:9"),
        ("character", "Sang Yao", "expressions", "1:1"),
        ("costume", "Cloak", "flat", "3:2"),
        ("prop", "Jade Seal", "turnaround", "16:9"),
    ],
)
async def test_the_slot_decides_the_frame(
    svc, imagegen, register, asset_type, name, slot, expected
):
    a = await _asset(svc, asset_type=asset_type, name=name)
    preview = await svc.preview_generate_slot(int(a["id"]), SCOPE, slot, None)
    assert preview["aspect_ratio"] == expected
    await svc.generate_slot(int(a["id"]), SCOPE, USER, _req(slot=slot))
    assert imagegen.calls[0]["aspect_ratio"] == expected
