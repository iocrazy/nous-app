"""The production shot-cut bench's pure parts: duration parsing, the
deterministic sample, and the default-user pick. The DB / storage / ffmpeg
path is the same code the index_shots workflow runs and is not re-tested
here."""

from __future__ import annotations

import pytest

from app.services.library.shot_cut import DEFAULT_PARAMS
from scripts.bench_shot_cut_prod import (
    _top_creator,
    choose_sample,
    parse_duration_seconds,
    parse_variants,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("93", 93.0),
        ("93.5", 93.5),
        ("1:33", 93.0),
        ("0:01:33", 93.0),
        ("01:02:03", 3723.0),
        (None, None),
        ("", None),
        ("n/a", None),
        ("0", None),
        ("-5", None),
    ],
)
def test_parse_duration_seconds(raw, expected):
    assert parse_duration_seconds(raw) == expected


def _rows(n: int, duration: str = "120"):
    return [
        {
            "resource_id": 1000 + i,
            "creator_id": "u",
            "title": f"t{i}",
            "duration": duration,
        }
        for i in range(n)
    ]


def test_choose_sample_filters_by_duration_and_is_deterministic():
    rows = _rows(10) + [
        {"resource_id": 5, "creator_id": "u", "title": "short", "duration": "8"},
        {"resource_id": 6, "creator_id": "u", "title": "stream", "duration": "2:10:00"},
        {"resource_id": 7, "creator_id": "u", "title": "unknown", "duration": None},
    ]
    a = choose_sample(rows, limit=4, seed=7, min_seconds=20, max_seconds=900)
    b = choose_sample(rows, limit=4, seed=7, min_seconds=20, max_seconds=900)
    assert [r["resource_id"] for r in a] == [r["resource_id"] for r in b]
    assert len(a) == 4
    assert all(20 <= r["seconds"] <= 900 for r in a)
    assert {5, 6, 7}.isdisjoint({r["resource_id"] for r in a})
    c = choose_sample(rows, limit=4, seed=8, min_seconds=20, max_seconds=900)
    assert [r["resource_id"] for r in c] != [r["resource_id"] for r in a]


def test_choose_sample_returns_everything_eligible_when_under_the_limit():
    out = choose_sample(_rows(3), limit=30, seed=1, min_seconds=20, max_seconds=900)
    assert [r["resource_id"] for r in out] == [1000, 1001, 1002]


def test_top_creator_is_the_one_with_most_videos():
    rows = [{"creator_id": "a"}, {"creator_id": "b"}, {"creator_id": "b"}]
    assert _top_creator(rows) == "b"
    assert _top_creator([]) is None


def test_parse_variants_always_starts_with_the_defaults_and_types_fields():
    out = parse_variants("min_shot_ms=800; min_shot_ms=800,ratio=2.0 ;;")
    assert [label for label, _ in out] == [
        "default",
        "min_shot_ms=800",
        "min_shot_ms=800,ratio=2.0",
    ]
    assert out[0][1] is DEFAULT_PARAMS
    assert out[1][1].min_shot_ms == 800 and isinstance(out[1][1].min_shot_ms, int)
    assert out[1][1].ratio == DEFAULT_PARAMS.ratio
    assert out[2][1].ratio == 2.0 and out[2][1].min_shot_ms == 800
    assert parse_variants("") == [("default", DEFAULT_PARAMS)]
    assert parse_variants(None) == [("default", DEFAULT_PARAMS)]


def test_parse_variants_takes_the_detector_as_a_string():
    out = parse_variants("detector=hist;scene_threshold=0.2,min_shot_ms=500")
    assert out[1][1].detector == "hist" and out[1][1].ratio == DEFAULT_PARAMS.ratio
    assert out[2][1].scene_threshold == 0.2 and out[2][1].min_shot_ms == 500
    assert out[2][1].detector == DEFAULT_PARAMS.detector == "scene"


@pytest.mark.parametrize(
    "bad", ["min_shot=800", "ratio", "min_shot_ms=abc", "detector=magic"]
)
def test_parse_variants_rejects_typos_instead_of_scoring_defaults(bad):
    with pytest.raises(ValueError):
        parse_variants(bad)
