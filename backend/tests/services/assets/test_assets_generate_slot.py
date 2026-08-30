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
        self.stamped: list[tuple[int, int]] = []
        self._missing = missing

    async def set_source_asset(self, gen_id, asset_id):
        self.stamped.append((int(gen_id), int(asset_id)))
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
    assert gen_repo.stamped == [(int(g), int(a["id"])) for g in out["generation_ids"]]

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
