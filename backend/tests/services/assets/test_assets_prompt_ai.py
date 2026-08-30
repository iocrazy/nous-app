"""Prompt translate / regenerate on the asset shelf (P2 Task 3).

Both endpoints drive an EXISTING agent path — the translation agent
(``TranslateService``) and the caption vision agent — so the tests here fake
exactly those two callables (``resource_ai_ops.translate_fields`` /
``resource_ai_ops.caption_resource_for_caller``, imported into
``assets_service``) and let everything else run for real. No DB, no network.

The behaviours that used to have a silent failure mode, and are pinned below:

* **never clobber** — a translate that overwrote a hand-written ``_zh`` field
  would destroy work with no undo and report 200. Non-empty targets are skipped
  unless ``force``; when nothing is left the answer is a typed 422, not a
  cheerful "translated 0 fields".
* **provider failure is a 503, not a 500** — ``AllModelsFailed`` /
  ``LLMCallError`` reach the user as ``translate_unavailable`` /
  ``caption_unavailable`` carrying the provider's own detail. Swallowed into
  the catch-all they would be a bare 500 whose body is masked to "Internal
  server error".
* **regenerate reads the PRIMARY slot** — the lowest ``sort_order`` file of
  ``PRIMARY_SLOT[asset_type]``, not "whatever list_files returned first". An
  empty slot is a 422 the user can act on ("attach a sheet"), never a caption
  run over an unrelated reference image.
* **presets stay read-only** — both paths write header columns, so both go
  through ``_require_writable``.
"""

from __future__ import annotations

import pytest

from app.schemas.assets import (
    AssetCreate,
    AttachFileRequest,
    PromptTranslateRequest,
)
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from app.services.library.resource_ai_ops import (
    CaptionAgentFailed,
    CaptionAgentPaused,
    CaptionSourceUnavailable,
    build_translate_plan,
)
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


class FakeTranslate:
    """Stands in for ``translate_fields`` — the ONE agent call on this path.

    Records the plan it was handed (that is what the never-clobber rule is
    asserted through) and answers with a deterministic marker per field.
    """

    def __init__(self, result=None, raises=None):
        self.calls: list[dict] = []
        self._result = result
        self._raises = raises

    async def __call__(self, plan, *, target_lang, user_id, resource_id=None):
        self.calls.append(
            {
                "plan": list(plan),
                "target_lang": target_lang,
                "user_id": user_id,
                "resource_id": resource_id,
            }
        )
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return dict(self._result)
        return {target: f"[{target_lang}] {text}" for _src, target, text in plan}


class FakeCaption:
    def __init__(self, result=None, raises=None):
        self.calls: list[tuple[str, str]] = []
        self._result = result or {"en": "a rooftop at dusk", "zh": "黄昏的屋顶"}
        self._raises = raises

    async def __call__(self, resource_id, user_id):
        self.calls.append((resource_id, user_id))
        if self._raises is not None:
            raise self._raises
        return dict(self._result)


@pytest.fixture
def translate(monkeypatch):
    fake = FakeTranslate()
    monkeypatch.setattr(assets_service, "translate_fields", fake)
    return fake


@pytest.fixture
def caption(monkeypatch):
    fake = FakeCaption()
    monkeypatch.setattr(assets_service, "caption_resource_for_caller", fake)
    return fake


async def _asset(svc, asset_type="character", name="Sang Yao", **extra):
    return await svc.create_asset(
        SCOPE, AssetCreate(asset_type=asset_type, name=name, **extra), USER
    )


def _req(target_lang="zh", force=False):
    return PromptTranslateRequest(target_lang=target_lang, force=force)


# ── build_translate_plan over the ASSET field pairs ─────────────────────────


def test_plan_reads_the_en_side_for_target_zh():
    row = {"prompt_positive": "a rooftop", "prompt_negative": "blurry"}
    plan = build_translate_plan(
        row, "zh", field_pairs=assets_service.ASSET_PROMPT_FIELD_PAIRS
    )
    assert plan == [
        ("prompt_positive", "prompt_positive_zh", "a rooftop"),
        ("prompt_negative", "prompt_negative_zh", "blurry"),
    ]


def test_plan_reads_the_zh_side_for_target_en():
    row = {"prompt_positive_zh": "屋顶", "prompt_negative_zh": "  "}
    plan = build_translate_plan(
        row, "en", field_pairs=assets_service.ASSET_PROMPT_FIELD_PAIRS
    )
    assert plan == [("prompt_positive_zh", "prompt_positive", "屋顶")], (
        "a whitespace-only source is not text to translate — including it "
        "would spend a provider call to write blank over a real field"
    )


# ── translate ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_writes_both_zh_fields(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop", prompt_negative="blurry")
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert out["prompt_positive_zh"] == "[zh] a rooftop"
    assert out["prompt_negative_zh"] == "[zh] blurry"
    assert out["prompt_positive"] == "a rooftop", "the source side is never touched"
    assert translate.calls[0]["target_lang"] == "zh"
    assert translate.calls[0]["user_id"] == USER


@pytest.mark.asyncio
async def test_translate_only_the_non_empty_source(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop")
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert out["prompt_positive_zh"] == "[zh] a rooftop"
    assert out["prompt_negative_zh"] is None
    assert [p[0] for p in translate.calls[0]["plan"]] == ["prompt_positive"]


@pytest.mark.asyncio
async def test_translate_reverse_direction_writes_the_en_side(svc, translate):
    a = await _asset(svc, prompt_positive_zh="屋顶")
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("en"), USER)
    assert out["prompt_positive"] == "[en] 屋顶"
    assert out["prompt_positive_zh"] == "屋顶"


@pytest.mark.asyncio
async def test_translate_never_clobbers_a_non_empty_target(svc, translate):
    a = await _asset(
        svc,
        prompt_positive="a rooftop",
        prompt_negative="blurry",
        prompt_positive_zh="我亲手写的",
    )
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert out["prompt_positive_zh"] == "我亲手写的", "hand-written text survived"
    assert out["prompt_negative_zh"] == "[zh] blurry"
    assert [p[1] for p in translate.calls[0]["plan"]] == ["prompt_negative_zh"], (
        "the skipped field must not even be SENT — paying for a provider call "
        "whose result is then discarded is the same bug, only quieter"
    )


@pytest.mark.asyncio
async def test_force_overwrites_the_non_empty_target(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop", prompt_positive_zh="旧的")
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh", force=True), USER)
    assert out["prompt_positive_zh"] == "[zh] a rooftop"


@pytest.mark.asyncio
async def test_nothing_to_translate_when_every_source_is_empty(svc, translate):
    a = await _asset(svc)
    with pytest.raises(AssetError) as ei:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert ei.value.status == 422 and ei.value.code == "nothing_to_translate"
    assert not translate.calls, "no provider call for an empty plan"


@pytest.mark.asyncio
async def test_nothing_to_translate_names_force_only_when_force_was_not_used(
    svc, translate
):
    """With ``force`` the targets were never consulted, so "send force=true"
    would be advice the caller has already taken — and the real cause (both
    sources are empty) would go unsaid."""
    a = await _asset(svc)
    with pytest.raises(AssetError) as plain:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    with pytest.raises(AssetError) as forced:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh", force=True), USER)

    assert plain.value.code == forced.value.code == "nothing_to_translate"
    assert "force=true" in plain.value.detail
    assert "force=true" not in forced.value.detail
    assert "empty on the source side" in forced.value.detail


@pytest.mark.asyncio
async def test_nothing_to_translate_when_every_target_is_already_filled(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop", prompt_positive_zh="屋顶")
    with pytest.raises(AssetError) as ei:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert ei.value.status == 422 and ei.value.code == "nothing_to_translate"
    assert not translate.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_name", ["AllModelsFailed", "LLMCallError"])
async def test_provider_failure_is_a_503_carrying_the_detail(
    svc, monkeypatch, exc_name
):
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
    from app.services.ai.llm.llm_retry_middleware import LLMCallError

    exc = {"AllModelsFailed": AllModelsFailed, "LLMCallError": LLMCallError}[exc_name](
        "qwen-max: 429 rate limited"
    )
    monkeypatch.setattr(assets_service, "translate_fields", FakeTranslate(raises=exc))
    a = await _asset(svc, prompt_positive="a rooftop")
    with pytest.raises(AssetError) as ei:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert ei.value.status == 503 and ei.value.code == "translate_unavailable"
    assert "429 rate limited" in ei.value.detail, (
        "the provider's own reason is the only actionable part — a generic "
        "'translation failed' sends the user to the wrong setting"
    )


@pytest.mark.asyncio
async def test_a_non_provider_exception_is_not_disguised_as_503(svc, monkeypatch):
    """A bug in our own code must stay a 500. Folding it into
    ``translate_unavailable`` would tell the user to check their provider
    configuration about a defect that has nothing to do with it."""
    monkeypatch.setattr(
        assets_service, "translate_fields", FakeTranslate(raises=KeyError("oops"))
    )
    a = await _asset(svc, prompt_positive="a rooftop")
    with pytest.raises(KeyError):
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)


@pytest.mark.asyncio
async def test_agent_returning_nothing_is_a_503_not_a_silent_200(svc, monkeypatch):
    monkeypatch.setattr(assets_service, "translate_fields", FakeTranslate(result={}))
    a = await _asset(svc, prompt_positive="a rooftop")
    with pytest.raises(AssetError) as ei:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert ei.value.status == 503 and ei.value.code == "translate_unavailable"


@pytest.mark.asyncio
async def test_translate_bumps_updated_at(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop")
    before = svc.assets.rows[int(a["id"])]["updated_at"]
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert svc.assets.rows[int(a["id"])]["updated_at"] > before
    assert out["updated_at"] != a["updated_at"]


@pytest.mark.asyncio
async def test_translate_on_a_preset_is_403(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop")
    svc.assets.make_preset(int(a["id"]))
    with pytest.raises(AssetError) as ei:
        await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert not translate.calls


@pytest.mark.asyncio
async def test_translate_returns_the_detail_envelope(svc, translate):
    a = await _asset(svc, prompt_positive="a rooftop")
    out = await svc.translate_prompt(int(a["id"]), SCOPE, _req("zh"), USER)
    for key in ("files", "links", "linked_by", "loadouts", "readiness"):
        assert key in out


# ── regenerate ─────────────────────────────────────────────────────────────


async def _with_primary_file(svc, slot="sheet", asset_type="character", **extra):
    a = await _asset(svc, asset_type=asset_type, **extra)
    await svc.attach_file(
        int(a["id"]),
        SCOPE,
        AttachFileRequest(resource_id=IN_SCOPE_RESOURCE, slot=slot),
        USER,
    )
    return a


@pytest.mark.asyncio
async def test_regenerate_writes_both_prompt_sides(svc, caption):
    a = await _with_primary_file(svc)
    out = await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert out["prompt_positive"] == "a rooftop at dusk"
    assert out["prompt_positive_zh"] == "黄昏的屋顶"
    assert caption.calls == [(IN_SCOPE_RESOURCE, USER)]


@pytest.mark.asyncio
async def test_regenerate_writes_only_the_side_the_agent_returned(svc, monkeypatch):
    monkeypatch.setattr(
        assets_service,
        "caption_resource_for_caller",
        FakeCaption(result={"en": "a rooftop at dusk"}),
    )
    a = await _with_primary_file(svc)
    out = await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert out["prompt_positive"] == "a rooftop at dusk"
    assert out["prompt_positive_zh"] is None


@pytest.mark.asyncio
async def test_regenerate_picks_the_lowest_sort_order_primary_file(svc, caption):
    a = await _asset(svc)
    aid = int(a["id"])
    # Attached out of order, and with a non-primary slot in between: the file
    # the vision agent reads must be decided by (slot, sort_order), not by
    # attach order.
    svc.relations.in_scope_resources.update({111, 222, 333})
    await svc.relations.attach(aid, 333, "stills", sort_order=0)
    await svc.relations.attach(aid, 222, "sheet", sort_order=7)
    await svc.relations.attach(aid, 111, "sheet", sort_order=2)
    await svc.regenerate_prompt(aid, SCOPE, USER)
    assert caption.calls == [("111", USER)]


@pytest.mark.asyncio
async def test_regenerate_without_a_primary_file_is_422(svc, caption):
    a = await _asset(svc)
    await svc.attach_file(
        int(a["id"]),
        SCOPE,
        AttachFileRequest(resource_id=IN_SCOPE_RESOURCE, slot="stills"),
        USER,
    )
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 422 and ei.value.code == "no_primary_file"
    assert "sheet" in ei.value.detail, "the reply must name the slot to fill"
    assert not caption.calls


@pytest.mark.asyncio
async def test_regenerate_on_a_prompt_asset_is_422_not_applicable(svc, caption):
    a = await _asset(svc, asset_type="prompt", name="Rooftop preset")
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 422 and ei.value.code == "not_applicable"
    assert not caption.calls


@pytest.mark.asyncio
async def test_regenerate_on_a_preset_is_403(svc, caption):
    a = await _with_primary_file(svc)
    svc.assets.make_preset(int(a["id"]))
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert not caption.calls


@pytest.mark.asyncio
async def test_regenerate_when_the_agent_fails_is_503(svc, monkeypatch):
    monkeypatch.setattr(
        assets_service,
        "caption_resource_for_caller",
        FakeCaption(raises=CaptionAgentFailed("the caption agent returned nothing")),
    )
    a = await _with_primary_file(svc)
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 503 and ei.value.code == "caption_unavailable"
    assert "returned nothing" in ei.value.detail


@pytest.mark.asyncio
async def test_paused_caption_agent_gets_its_own_code(svc, monkeypatch):
    """ "Resume the caption agent" is a different action from "the provider is
    unreachable". Sharing ``caption_unavailable`` would make the UI unable to
    say which one happened — and a paused agent is the one the user can fix in
    one click."""
    monkeypatch.setattr(
        assets_service,
        "caption_resource_for_caller",
        FakeCaption(raises=CaptionAgentPaused("The caption agent is paused (budget)")),
    )
    a = await _with_primary_file(svc)
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 503 and ei.value.code == "caption_paused"
    assert "budget" in ei.value.detail


@pytest.mark.asyncio
async def test_regenerate_provider_failure_is_503(svc, monkeypatch):
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed

    monkeypatch.setattr(
        assets_service,
        "caption_resource_for_caller",
        FakeCaption(raises=AllModelsFailed("qwen-vl: 401 unauthorized")),
    )
    a = await _with_primary_file(svc)
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 503 and ei.value.code == "caption_unavailable"
    assert "401 unauthorized" in ei.value.detail


@pytest.mark.asyncio
async def test_regenerate_when_the_file_has_nothing_to_look_at_is_422(svc, monkeypatch):
    """ "This album is captioned per slide" is a DIFFERENT answer from "the
    provider is down" — collapsing the two sends the user to Settings → AI
    about a file that will never be captionable."""
    monkeypatch.setattr(
        assets_service,
        "caption_resource_for_caller",
        FakeCaption(raises=CaptionSourceUnavailable("Albums go one slide at a time")),
    )
    a = await _with_primary_file(svc)
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 422 and ei.value.code == "file_not_captionable"
    assert "slide" in ei.value.detail


@pytest.mark.asyncio
async def test_regenerate_refuses_a_file_that_left_the_scope(svc, caption):
    """The attach path checked scope once, at attach time. A resource can leave
    the scope afterwards, and this path hands its BYTES to a vision model and
    writes the result onto the asset — so it re-checks."""
    a = await _with_primary_file(svc)
    svc.relations.in_scope_resources.discard(int(IN_SCOPE_RESOURCE))
    with pytest.raises(AssetError) as ei:
        await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert ei.value.status == 404 and ei.value.code == "resource_not_found"
    assert not caption.calls


@pytest.mark.asyncio
async def test_regenerate_bumps_updated_at(svc, caption):
    a = await _with_primary_file(svc)
    before = svc.assets.rows[int(a["id"])]["updated_at"]
    await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert svc.assets.rows[int(a["id"])]["updated_at"] > before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asset_type,slot",
    [
        ("character", "sheet"),
        ("location", "establishing"),
        ("prop", "turnaround"),
        ("costume", "flat"),
        ("audio", "primary"),
    ],
)
async def test_regenerate_uses_each_types_primary_slot(svc, caption, asset_type, slot):
    """The slot table is the single source of truth — a second hardcoded copy
    inside this method is exactly how the two drift apart."""
    a = await _with_primary_file(svc, slot=slot, asset_type=asset_type, name=slot)
    await svc.regenerate_prompt(int(a["id"]), SCOPE, USER)
    assert caption.calls == [(IN_SCOPE_RESOURCE, USER)]
