# backend/tests/services/test_caption_service_parse.py

"""Unit tests for the caption agent's output parsing + image encoding."""

import json

from app.services.ai.caption.caption_service import (
    _encode_image_sync,
    parse_caption_json,
    parse_caption_result,
)


class TestParseCaptionJson:
    def test_plain_json(self):
        out = parse_caption_json(
            json.dumps({"en": "a cat, masterpiece", "zh": "一只猫, masterpiece"})
        )
        assert out == {"en": "a cat, masterpiece", "zh": "一只猫, masterpiece"}

    def test_fenced_json(self):
        payload = '```json\n{"en": "a dog", "zh": "一只狗"}\n```'
        assert parse_caption_json(payload) == {"en": "a dog", "zh": "一只狗"}

    def test_missing_side_kept_partial(self):
        assert parse_caption_json('{"en": "a fox"}') == {"en": "a fox"}

    def test_blank_sides_dropped(self):
        assert parse_caption_json('{"en": "  ", "zh": "城市夜景"}') == {
            "zh": "城市夜景"
        }

    def test_non_string_values_dropped(self):
        assert parse_caption_json('{"en": ["a", "b"], "zh": 42}') == {}

    def test_garbage_returns_empty(self):
        assert parse_caption_json("not json at all") == {}
        assert parse_caption_json("") == {}
        assert parse_caption_json('["a", "list"]') == {}


class TestParseCaptionResult:
    def test_full_structured_json(self):
        payload = {
            "prompt_en": "a cat, masterpiece",
            "prompt_zh": "一只猫, masterpiece",
            "prompt_json": {
                "subject": "a cat",
                "style": "photorealistic",
                "composition": "centered",
                "lighting": "soft studio light",
                "color": "warm tones",
                "aspect_ratio": "1:1",
            },
            "tags": [
                {"en": "cat", "zh": "猫"},
                {"en": "studio", "zh": "影棚"},
            ],
            "category": "Photography",
            "aspect_ratio": "1:1",
        }
        out = parse_caption_result(json.dumps(payload))
        assert out["en"] == "a cat, masterpiece"
        assert out["zh"] == "一只猫, masterpiece"
        assert out["prompt_json"] == {
            "subject": "a cat",
            "style": "photorealistic",
            "composition": "centered",
            "lighting": "soft studio light",
            "color": "warm tones",
            "aspect_ratio": "1:1",
        }
        assert out["tags"] == [
            {"en": "cat", "zh": "猫"},
            {"en": "studio", "zh": "影棚"},
        ]
        assert out["category"] == "Photography"

    def test_fenced_full_json(self):
        payload = (
            '```json\n{"prompt_en": "a dog", "prompt_zh": "一只狗", '
            '"prompt_json": {"subject": "a dog"}, "tags": [], '
            '"category": "Pets"}\n```'
        )
        out = parse_caption_result(payload)
        assert out["en"] == "a dog"
        assert out["zh"] == "一只狗"
        assert out["prompt_json"] == {"subject": "a dog"}
        assert "tags" not in out  # empty list dropped
        assert out["category"] == "Pets"

    def test_missing_prompt_json_fields_tolerated(self):
        payload = {
            "prompt_en": "a fox",
            "prompt_zh": "一只狐狸",
            "prompt_json": {"subject": "a fox", "style": "watercolor"},
        }
        out = parse_caption_result(json.dumps(payload))
        assert out["prompt_json"] == {"subject": "a fox", "style": "watercolor"}

    def test_top_level_aspect_ratio_backfills_prompt_json(self):
        payload = {
            "prompt_en": "a mountain",
            "prompt_json": {"subject": "a mountain"},
            "aspect_ratio": "16:9",
        }
        out = parse_caption_result(json.dumps(payload))
        assert out["prompt_json"]["aspect_ratio"] == "16:9"

    def test_tags_deduped_and_capped(self):
        payload = {
            "prompt_en": "x",
            "tags": [{"en": f"tag-{i}"} for i in range(10)]
            + [{"en": "tag-0"}],  # duplicate, case-sensitive-equal
        }
        out = parse_caption_result(json.dumps(payload))
        assert len(out["tags"]) == 6

    def test_tags_require_non_empty_en(self):
        payload = {"prompt_en": "x", "tags": [{"zh": "无英文"}, {"en": "  "}]}
        out = parse_caption_result(json.dumps(payload))
        assert "tags" not in out

    def test_missing_both_prompt_sides_falls_back_to_legacy_shape(self):
        # Structured envelope present but agent used the OLD field names —
        # parse_caption_result should recover via parse_caption_json.
        payload = '{"en": "a plain fallback prompt", "zh": "一个后备提示词"}'
        out = parse_caption_result(payload)
        assert out == {"en": "a plain fallback prompt", "zh": "一个后备提示词"}

    def test_garbage_json_falls_back_to_legacy_parser(self):
        assert parse_caption_result("not json at all") == {}
        assert parse_caption_result("") == {}

    def test_non_dict_json_falls_back(self):
        assert parse_caption_result('["a", "list"]') == {}

    def test_completely_empty_falls_back_to_empty(self):
        assert parse_caption_result("{}") == {}

    def test_category_ignored_when_blank(self):
        payload = {"prompt_en": "x", "category": "   "}
        out = parse_caption_result(json.dumps(payload))
        assert "category" not in out


class TestEncodeImageSync:
    def test_downscales_and_returns_data_url(self, tmp_path):
        from PIL import Image

        src = tmp_path / "big.png"
        Image.new("RGB", (2048, 1024), color=(200, 30, 30)).save(src)
        url = _encode_image_sync(str(src))
        assert url is not None
        assert url.startswith("data:image/jpeg;base64,")

    def test_missing_file_returns_none(self, tmp_path):
        assert _encode_image_sync(str(tmp_path / "absent.png")) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"definitely not an image")
        assert _encode_image_sync(str(bad)) is None
