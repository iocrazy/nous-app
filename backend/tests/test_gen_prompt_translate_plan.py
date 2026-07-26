"""Unit tests for build_translate_plan — which prompt fields get translated."""

from app.api.resources_ai_router import build_translate_plan


def test_zh_target_translates_both_sides():
    resource = {"gen_prompt": "a cat", "gen_prompt_negative": "lowres"}
    plan = build_translate_plan(resource, "zh")
    assert plan == [
        ("gen_prompt", "gen_prompt_zh", "a cat"),
        ("gen_prompt_negative", "gen_prompt_negative_zh", "lowres"),
    ]


def test_en_target_reads_zh_sides():
    resource = {"gen_prompt_zh": "一只猫", "gen_prompt_negative_zh": "低分辨率"}
    plan = build_translate_plan(resource, "en")
    assert plan == [
        ("gen_prompt_zh", "gen_prompt", "一只猫"),
        ("gen_prompt_negative_zh", "gen_prompt_negative", "低分辨率"),
    ]


def test_skips_empty_sides():
    plan = build_translate_plan({"gen_prompt": "a cat", "gen_prompt_negative": "  "}, "zh")
    assert plan == [("gen_prompt", "gen_prompt_zh", "a cat")]


def test_all_empty_gives_empty_plan():
    assert build_translate_plan({}, "zh") == []
