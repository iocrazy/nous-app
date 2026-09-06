import pytest

from app.services.prompts.catalog_service import PromptCatalogService

NOW = "2026-09-05T00:00:00+00:00"
SCOPE = 331438215859255


class FakeAssets:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list(self, scope_id, **kw):
        self.calls.append((scope_id, kw))
        return [r for r in self.rows if r["asset_type"] == kw.get("asset_type")]


class FakeCatalog:
    def __init__(self, resources, examples=None):
        self.resources = resources
        self.examples = examples or {}
        self.calls = []
        self.example_calls = []
        self.limits = []

    async def list_prompted_resources(self, scope_id, *, project_id=None, limit=2000):
        self.calls.append(("resources", scope_id, project_id))
        self.limits.append(limit)
        return list(self.resources)

    async def example_file_ids(self, asset_ids):
        self.example_calls.append(list(asset_ids))
        return {a: self.examples.get(a, []) for a in asset_ids}


def asset(id_, name, **over):
    row = {
        "id": id_,
        "scope_id": SCOPE,
        "asset_type": "prompt",
        "name": name,
        "prompt_positive": "p",
        "prompt_negative": None,
        "prompt_positive_zh": None,
        "prompt_negative_zh": None,
        "platform_params": {},
        "cover_file_id": None,
        "tags": {},
        "is_system_preset": False,
        "in_library": True,
        "updated_at": NOW,
    }
    row.update(over)
    return row


def resource(id_, **over):
    row = {
        "id": id_,
        "filename": f"r{id_}.png",
        "media_id": None,
        "gen_prompt": "rain on the visor",
        "gen_prompt_zh": None,
        "gen_prompt_negative": None,
        "gen_prompt_negative_zh": None,
        "gen_params": {"steps": 20},
        "slide_prompts": None,
        "prompt_origin": "extracted",
        "updated_at": NOW,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_mine_merges_templates_and_pictures_with_counts():
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Tpl")]),
        catalog_repo=FakeCatalog(
            [
                resource(10),
                resource(
                    11,
                    gen_params=None,
                    gen_prompt_json='{"x":1}',
                    prompt_origin="captioned",
                ),
            ]
        ),
    )
    page = await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=60,
        offset=0,
    )
    assert page["total"] == 3
    assert page["by_form"] == {"template": 1, "image": 2, "album": 0}
    assert page["by_origin"] == {"typed": 1, "extracted": 1, "captioned": 1}
    assert [e["key"] for e in page["items"]] == [
        "template:1",
        "image:10",
        "image:11",
    ]  # captioned last


@pytest.mark.asyncio
async def test_form_origin_query_filters_apply_after_counting():
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Rain template")]),
        catalog_repo=FakeCatalog([resource(10), resource(12, gen_prompt="tiger")]),
    )
    page = await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form="image",
        origin=None,
        q="rain",
        limit=60,
        offset=0,
    )
    assert [e["key"] for e in page["items"]] == ["image:10"]
    # counts describe the UNFILTERED segment so the chips can show what a filter would reveal
    # total is the unfiltered segment, like by_form — the page narrows only items (ruling R6)
    assert page["total"] == 3 and page["by_form"]["template"] == 1


@pytest.mark.asyncio
async def test_project_segment_passes_project_to_both_repos():
    assets = FakeAssets([])
    catalog = FakeCatalog([])
    svc = PromptCatalogService(assets_repo=assets, catalog_repo=catalog)
    await svc.list(
        SCOPE,
        segment="project",
        project_id=55,
        form=None,
        origin=None,
        q=None,
        limit=10,
        offset=0,
    )
    assert (
        assets.calls[0][1]["project_id"] == 55
        and assets.calls[0][1]["library"] == "all"
    )
    assert catalog.calls == [("resources", SCOPE, 55)]


@pytest.mark.asyncio
async def test_system_segment_only_returns_presets_and_no_pictures():
    assets = FakeAssets([asset(2, "Preset", is_system_preset=True), asset(3, "Mine")])
    catalog = FakeCatalog([resource(10)])
    svc = PromptCatalogService(assets_repo=assets, catalog_repo=catalog)
    page = await svc.list(
        SCOPE,
        segment="system",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=10,
        offset=0,
    )
    assert [e["key"] for e in page["items"]] == ["template:2"]
    assert catalog.calls == []


@pytest.mark.asyncio
async def test_project_segment_without_project_id_is_422():
    from app.services.assets.assets_service import AssetError

    svc = PromptCatalogService(assets_repo=FakeAssets([]), catalog_repo=FakeCatalog([]))
    with pytest.raises(AssetError) as ei:
        await svc.list(
            SCOPE,
            segment="project",
            project_id=None,
            form=None,
            origin=None,
            q=None,
            limit=10,
            offset=0,
        )
    assert ei.value.status == 422 and ei.value.code == "project_required"


@pytest.mark.asyncio
async def test_counts():
    svc = PromptCatalogService(
        assets_repo=FakeAssets(
            [asset(1, "Mine"), asset(2, "Preset", is_system_preset=True)]
        ),
        catalog_repo=FakeCatalog([resource(10)]),
    )
    assert await svc.counts(SCOPE, project_id=None) == {
        "mine": 2,
        "project": None,
        "system": 1,
    }


@pytest.mark.asyncio
async def test_counts_skips_the_thumbnail_lookup_that_list_needs():
    catalog = FakeCatalog([resource(10)])
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Tpl")]), catalog_repo=catalog
    )
    await svc.counts(SCOPE, project_id=None)
    # counts() only takes len(); a thumbnail query per segment is pure cost
    assert catalog.example_calls == []
    await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=60,
        offset=0,
    )
    assert catalog.example_calls == [[1]]


@pytest.mark.asyncio
async def test_templates_page_until_a_short_page_then_stop():
    rows = [asset(i, f"T{i}") for i in range(1, 451)]

    class PagingAssets(FakeAssets):
        async def list(self, scope_id, **kw):
            self.calls.append((scope_id, kw))
            off, lim = kw["offset"], kw["limit"]
            return self.rows[off : off + lim]

    assets = PagingAssets(rows)
    svc = PromptCatalogService(assets_repo=assets, catalog_repo=FakeCatalog([]))
    page = await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=1000,
        offset=0,
    )
    # 200 + 200 + 50: a single unpaged read would have stopped at the repo's cap of 200
    assert [c[1]["offset"] for c in assets.calls] == [0, 200, 400]
    assert page["total"] == 450


@pytest.mark.asyncio
async def test_pictures_read_is_bounded_by_the_service_ceiling():
    from app.services.prompts.catalog_service import _PICTURE_CEILING

    catalog = FakeCatalog([resource(10)])
    svc = PromptCatalogService(assets_repo=FakeAssets([]), catalog_repo=catalog)
    await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=60,
        offset=0,
    )
    # Unbounded, this read materializes every prompted resource in the scope.
    assert catalog.limits == [_PICTURE_CEILING]


@pytest.mark.asyncio
async def test_a_row_whose_only_text_is_a_sentinel_is_not_a_card():
    """The SQL row filter and the Python blank rule disagree on purpose.

    ``media_repository.has_prompt_expr`` selects any non-whitespace text, so a
    row whose only positive is the literal ``'[]'`` reaches the builder; every
    text field then renders as ``None`` (``origin.is_blank_text``). Shown, it
    is a card with a title, no body and both actions disabled — and no
    explanation. It is dropped at the entry seam instead, so ``total`` and the
    badge agree with what the shelf lists.
    """
    catalog = FakeCatalog(
        [resource(10), resource(13, gen_prompt="[]", gen_params=None)]
    )
    svc = PromptCatalogService(assets_repo=FakeAssets([]), catalog_repo=catalog)
    page = await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=60,
        offset=0,
    )
    assert [e["key"] for e in page["items"]] == ["image:10"]
    assert page["total"] == 1
    assert await svc.counts(SCOPE, project_id=None) == {
        "mine": 1,
        "project": None,
        "system": 0,
    }


@pytest.mark.asyncio
async def test_an_album_whose_first_slide_is_textless_is_still_a_card():
    """The drop rule reads the slides too.

    An album's own positive is its FIRST slide's text, so an album whose
    slide 1 is blank but whose slide 2 is not would look textless at the row
    level while carrying exactly what the user came for.
    """
    catalog = FakeCatalog(
        [
            resource(
                14,
                media_id=77,
                gen_prompt=None,
                slide_prompts={"01.jpg": {}, "02.jpg": {"en": "a lit alley"}},
            )
        ]
    )
    svc = PromptCatalogService(assets_repo=FakeAssets([]), catalog_repo=catalog)
    page = await svc.list(
        SCOPE,
        segment="mine",
        project_id=None,
        form=None,
        origin=None,
        q=None,
        limit=60,
        offset=0,
    )
    assert [e["key"] for e in page["items"]] == ["album:14"]


def test_pictures_read_runs_under_system_request_scope():
    """Mirrors the backfill's pin (``test_backfill_resource_prompt_origin.py:28``).

    ``Resources`` is UserScoped and ``SCOPE_ENFORCE_RESOURCES`` is on in
    production ONLY, so a refactor that hoists or drops this ``async with``
    keeps every unit test green and 500s the Prompts page on deploy.
    """
    from pathlib import Path

    import app.services.prompts.catalog_service as mod

    source = Path(mod.__file__).read_text()
    assert "system_request_scope(" in source
    assert source.index("system_request_scope(") < source.index(
        "list_prompted_resources("
    )
