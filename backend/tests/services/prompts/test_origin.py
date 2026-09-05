import pytest

from app.services.prompts.origin import (
    PROMPT_ORIGINS,
    derive_origin,
    is_blank_text,
    stamp_origin,
)


def test_origins_are_the_three_the_check_allows():
    assert PROMPT_ORIGINS == ("typed", "extracted", "captioned")


def test_stamp_adds_origin_only_when_text_is_written():
    assert stamp_origin({"gen_prompt": "x"}, "typed") == {
        "gen_prompt": "x",
        "prompt_origin": "typed",
    }
    assert (
        stamp_origin({"gen_prompt_zh": "x"}, "captioned")["prompt_origin"]
        == "captioned"
    )
    assert (
        stamp_origin({"slide_prompts": {"a.jpg": {"en": "x"}}}, "captioned")[
            "prompt_origin"
        ]
        == "captioned"
    )
    # Negative-only or params-only writes do not change who wrote the positive text.
    assert stamp_origin({"gen_prompt_negative": "x"}, "typed") == {
        "gen_prompt_negative": "x"
    }
    assert stamp_origin({"gen_params": {"steps": 1}}, "extracted") == {
        "gen_params": {"steps": 1}
    }


def test_stamp_refuses_unknown_origin():
    with pytest.raises(ValueError):
        stamp_origin({"gen_prompt": "x"}, "guessed")


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"gen_prompt": "", "gen_prompt_zh": None, "slide_prompts": None}, None),
        ({"gen_prompt": "[]"}, None),
        ({"gen_prompt": "a", "gen_params": {"steps": 20}}, "extracted"),
        (
            {"gen_prompt": "a", "gen_params": {}, "gen_prompt_json": '{"subject":"x"}'},
            "captioned",
        ),
        ({"gen_prompt": None, "slide_prompts": {"002.jpg": {"en": "a"}}}, "captioned"),
        ({"gen_prompt": "a", "gen_params": None, "gen_prompt_json": None}, "typed"),
        ({"gen_prompt_zh": "中文", "gen_prompt_json": "null"}, "typed"),
    ],
)
def test_derive_origin_backfill_rule(row, expected):
    assert derive_origin(row) == expected


@pytest.mark.parametrize(
    ("value", "blank"),
    [
        ("", True),
        ("  ", True),
        ("[]", True),
        ('""', True),
        ("null", True),
        ("{}", True),
        ("a", False),
        ("null pointer", False),
    ],
)
def test_is_blank_text_sentinels(value, blank):
    assert is_blank_text(value) is blank
