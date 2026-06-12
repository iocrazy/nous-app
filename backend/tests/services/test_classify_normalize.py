# backend/tests/services/test_classify_normalize.py

"""Unit tests for classification normalization (IC normalize port)."""

from app.services.ai.classify.classify_service import parse_classification_json
from app.services.ai.classify.normalize import (
    MAX_TAGS_PER_DIMENSION,
    normalize_classification,
    sanitize_tag,
)


class TestSanitizeTag:
    def test_strips_hashes_separators_and_whitespace(self):
        assert sanitize_tag("  #暖色, ") == "暖色"
        assert sanitize_tag("＃＃soft   light") == "soft light"

    def test_caps_length(self):
        assert len(sanitize_tag("x" * 100)) == 24

    def test_empty_and_none(self):
        assert sanitize_tag(None) == ""
        assert sanitize_tag("  ,，、  ") == ""


class TestNormalizeClassification:
    def test_flattens_known_dimensions(self):
        raw = {
            "summary": "a cozy living room",
            "dimensions": {
                "environment": [{"en": "indoor", "zh": "室内"}],
                "lighting": [
                    {"en": "soft light", "zh": "柔光"},
                    {"en": "warm light", "zh": "暖光"},
                ],
            },
        }
        tags = normalize_classification(raw)
        assert [(t.dimension, t.en, t.zh) for t in tags] == [
            ("environment", "indoor", "室内"),
            ("lighting", "soft light", "柔光"),
            ("lighting", "warm light", "暖光"),
        ]
        assert tags[0].group == "Environment"

    def test_unknown_dimension_dropped(self):
        raw = {"dimensions": {"made_up": [{"en": "thing", "zh": "东西"}]}}
        assert normalize_classification(raw) == []

    def test_en_required_zh_optional(self):
        raw = {
            "dimensions": {
                "style": [{"zh": "写实"}, {"en": "minimalist"}],
            }
        }
        tags = normalize_classification(raw)
        assert len(tags) == 1
        assert tags[0].en == "minimalist"
        assert tags[0].zh == ""

    def test_dedup_case_insensitive_per_dimension(self):
        raw = {
            "dimensions": {
                "color": [
                    {"en": "Warm Tones", "zh": "暖色"},
                    {"en": "warm tones", "zh": "暖色调"},
                ]
            }
        }
        assert len(normalize_classification(raw)) == 1

    def test_per_dimension_cap(self):
        raw = {
            "dimensions": {
                "mood": [{"en": f"mood-{i}", "zh": f"氛围{i}"} for i in range(10)]
            }
        }
        assert len(normalize_classification(raw)) == MAX_TAGS_PER_DIMENSION

    def test_garbage_shapes(self):
        assert normalize_classification(None) == []
        assert normalize_classification("text") == []
        assert normalize_classification({"dimensions": "nope"}) == []
        assert (
            normalize_classification({"dimensions": {"style": [{"en": 42}, "str"]}})
            == []
        )


class TestParseClassificationJson:
    def test_fenced(self):
        payload = '```json\n{"dimensions": {}}\n```'
        assert parse_classification_json(payload) == {"dimensions": {}}

    def test_garbage(self):
        assert parse_classification_json("nope") == {}
        assert parse_classification_json('["list"]') == {}
