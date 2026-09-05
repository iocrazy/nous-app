import inspect

import pytest

from app.schemas.prompts import PromptEntry
from app.services.prompts import entries as entries_module
from app.services.prompts.entries import (
    entry_from_asset,
    entry_from_resource,
    matches_query,
    sort_entries,
    title_from_filename,
)

ASSET = {
    "id": 11,
    "scope_id": 9000,
    "asset_type": "prompt",
    "name": "Rain-soaked close-up",
    "prompt_positive": "hero close-up, rain",
    "prompt_negative": "flare",
    "prompt_positive_zh": None,
    "prompt_negative_zh": None,
    "platform_params": {},
    "cover_file_id": 501,
    "tags": {"group": ["Lighting"], "mood": ["cold"]},
    "is_system_preset": False,
    "updated_at": "2026-09-05T00:00:00+00:00",
}
IMAGE = {
    "id": 346256147694349,
    "filename": "bicycle-oranges.png",
    "media_id": None,
    "gen_prompt": "cheerful woman, oranges",
    "gen_prompt_zh": None,
    "gen_prompt_negative": "lens flare",
    "gen_prompt_negative_zh": None,
    "gen_params": {"tool": "a1111", "width": 832, "height": 1216, "steps": 28},
    "slide_prompts": None,
    "prompt_origin": "extracted",
    "updated_at": "2026-09-03T00:00:00+00:00",
}
ALBUM = {
    **IMAGE,
    "id": 7,
    "filename": "orange-harvest",
    "media_id": 99,
    "gen_prompt": None,
    "gen_params": None,
    "prompt_origin": "captioned",
    "slide_prompts": {
        "003.jpg": {"en": "leaning on bicycle"},
        "002.jpg": {"en": "winking", "zh": "眨眼", "neg_en": "blur"},
    },
}


def test_template_entry_shape_and_thumbs():
    e = entry_from_asset(ASSET, example_resource_ids=[601, 602])
    PromptEntry.model_validate(e)
    assert (
        e["key"] == "template:11" and e["form"] == "template" and e["origin"] == "typed"
    )
    assert e["tags"] == ["Lighting", "cold"]
    # cover first, then examples, capped at 3
    assert [t["url"] for t in e["thumbs"]] == [
        "/api/v1/resources/501/cover",
        "/api/v1/resources/601/cover",
        "/api/v1/resources/602/cover",
    ]
    assert e["source"] == {"store": "assets", "id": "11"}


def test_template_without_cover_uses_examples_only():
    e = entry_from_asset({**ASSET, "cover_file_id": None}, example_resource_ids=[])
    assert e["thumbs"] == []


def test_image_entry_shape():
    e = entry_from_resource(IMAGE)
    PromptEntry.model_validate(e)
    assert e["key"] == "image:346256147694349" and e["form"] == "image"
    assert e["title"] == "bicycle-oranges" and e["origin"] == "extracted"
    assert (
        e["positive_en"] == "cheerful woman, oranges"
        and e["negative_en"] == "lens flare"
    )
    assert e["params"]["width"] == 832
    assert e["thumbs"] == [
        {"url": "/api/v1/resources/346256147694349/cover", "kind": "image"}
    ]
    assert e["slides"] is None and e["source"] == {
        "store": "uploads",
        "id": "346256147694349",
    }


def test_album_entry_lists_slides_sorted_by_name_with_urls():
    e = entry_from_resource(ALBUM)
    PromptEntry.model_validate(e)
    assert e["form"] == "album" and e["key"] == "album:7"
    assert [s["name"] for s in e["slides"]] == ["002.jpg", "003.jpg"]
    assert e["slides"][0]["url"] == "/api/v1/media/99/slides/002.jpg"
    assert (
        e["slides"][0]["positive_zh"] == "眨眼"
        and e["slides"][0]["negative_en"] == "blur"
    )
    assert e["slides"][1]["positive_zh"] is None
    # the album's own positive is the first slide's, so a list row has a first line
    assert e["positive_en"] == "winking"
    assert [t["url"] for t in e["thumbs"]] == [
        "/api/v1/media/99/slides/002.jpg",
        "/api/v1/media/99/slides/003.jpg",
    ]


def test_album_without_media_id_has_no_slide_urls_but_still_lists_text():
    e = entry_from_resource({**ALBUM, "media_id": None})
    assert e["slides"][0]["url"] is None and e["thumbs"] == []


def test_title_from_filename():
    assert title_from_filename("62d1e7d1f9d4b634.PNG") == "62d1e7d1f9d4b634"
    assert title_from_filename("no-extension") == "no-extension"
    assert title_from_filename("") == "Untitled"


def test_matches_query_looks_at_title_text_and_tags():
    e = entry_from_asset(ASSET, example_resource_ids=[])
    assert (
        matches_query(e, "RAIN")
        and matches_query(e, "lighting")
        and not matches_query(e, "tiger")
    )
    assert matches_query(e, "")


def test_sort_puts_captioned_last_then_recent_first():
    a = {"origin": "extracted", "updated_at": "2026-09-01T00:00:00+00:00", "key": "a"}
    b = {"origin": "captioned", "updated_at": "2026-09-09T00:00:00+00:00", "key": "b"}
    c = {"origin": None, "updated_at": "2026-09-05T00:00:00+00:00", "key": "c"}
    d = {"origin": "typed", "updated_at": "2026-09-04T00:00:00+00:00", "key": "d"}
    assert [x["key"] for x in sort_entries([a, b, c, d])] == ["d", "a", "b", "c"]


BLANK_SENTINELS = ["", "  ", "[]", '""', "null", "{}"]


@pytest.mark.parametrize("value", BLANK_SENTINELS)
def test_blank_sentinels_render_as_missing(value):
    assert entry_from_resource({**IMAGE, "gen_prompt": value})["positive_en"] is None
    assert (
        entry_from_asset({**ASSET, "prompt_positive": value}, example_resource_ids=[])[
            "positive_en"
        ]
        is None
    )


def test_entries_does_not_keep_its_own_blank_set():
    """One owner: origin.BLANK_TEXT. A local copy here could drift unnoticed."""
    assert "_BLANK =" not in inspect.getsource(entries_module)
