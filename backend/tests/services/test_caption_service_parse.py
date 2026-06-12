# backend/tests/services/test_caption_service_parse.py

"""Unit tests for the caption agent's output parsing + image encoding."""

import json

from app.services.ai.caption.caption_service import (
    _encode_image_sync,
    parse_caption_json,
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
