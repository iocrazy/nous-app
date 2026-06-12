# backend/tests/services/test_png_prompt_extractor.py

"""Unit tests for the PNG generation-prompt extractor.

Builds tiny in-memory PNGs (signature + hand-rolled chunks) so the parser
is exercised against real byte layouts without fixture files.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from app.services.library.png_prompt_extractor import (
    extract_comfyui_prompt,
    extract_png_prompt,
    parse_a1111_parameters,
)

A1111_BLOB = (
    "masterpiece, 1girl, silver hair,\nbacklit, golden hour\n"
    "Negative prompt: lowres, bad anatomy\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1234"
)


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data))
    )


def _png(*chunks: bytes) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + idat + iend


def _write(tmp_path: Path, payload: bytes) -> Path:
    p = tmp_path / "test.png"
    p.write_bytes(payload)
    return p


class TestParseA1111Parameters:
    def test_strips_negative_and_settings(self):
        prompt = parse_a1111_parameters(A1111_BLOB)
        assert prompt == "masterpiece, 1girl, silver hair,\nbacklit, golden hour"

    def test_prompt_only_blob(self):
        assert parse_a1111_parameters("a cat\n") == "a cat"

    def test_settings_without_negative(self):
        blob = "a cat\nSteps: 30, Sampler: DPM++"
        assert parse_a1111_parameters(blob) == "a cat"


class TestExtractComfyuiPrompt:
    def test_picks_longest_clip_text(self):
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "a sweeping mountain vista at dawn, mist"},
            },
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
            "3": {"class_type": "KSampler", "inputs": {"seed": 5}},
        }
        result = extract_comfyui_prompt(json.dumps(graph))
        assert result == "a sweeping mountain vista at dawn, mist"

    def test_linked_text_input_ignored(self):
        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ["4", 0]}},
        }
        assert extract_comfyui_prompt(json.dumps(graph)) is None

    def test_invalid_json(self):
        assert extract_comfyui_prompt("{not json") is None


class TestExtractPngPrompt:
    def test_a1111_text_chunk(self, tmp_path):
        data = b"parameters\x00" + A1111_BLOB.encode("latin-1")
        path = _write(tmp_path, _png(_chunk(b"tEXt", data)))
        prompt = extract_png_prompt(path)
        assert prompt is not None
        assert prompt.startswith("masterpiece, 1girl")
        assert "Negative prompt" not in prompt

    def test_a1111_itxt_chunk(self, tmp_path):
        # iTXt: keyword\0 compflag(0) compmethod(0) lang\0 transkw\0 utf8-text
        data = b"parameters\x00\x00\x00\x00\x00" + A1111_BLOB.encode("utf-8")
        path = _write(tmp_path, _png(_chunk(b"iTXt", data)))
        prompt = extract_png_prompt(path)
        assert prompt is not None
        assert prompt.startswith("masterpiece")

    def test_compressed_ztxt_chunk(self, tmp_path):
        data = b"parameters\x00\x00" + zlib.compress(A1111_BLOB.encode("latin-1"))
        path = _write(tmp_path, _png(_chunk(b"zTXt", data)))
        prompt = extract_png_prompt(path)
        assert prompt is not None
        assert prompt.startswith("masterpiece")

    def test_comfyui_prompt_chunk(self, tmp_path):
        graph = {
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "cyberpunk alley, neon rain, cinematic"},
            }
        }
        data = b"prompt\x00" + json.dumps(graph).encode("latin-1")
        path = _write(tmp_path, _png(_chunk(b"tEXt", data)))
        assert extract_png_prompt(path) == "cyberpunk alley, neon rain, cinematic"

    def test_a1111_wins_over_comfyui(self, tmp_path):
        comfy = b"prompt\x00" + json.dumps(
            {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy"}}}
        ).encode("latin-1")
        a1111 = b"parameters\x00a cat\nSteps: 9"
        path = _write(tmp_path, _png(_chunk(b"tEXt", comfy), _chunk(b"tEXt", a1111)))
        assert extract_png_prompt(path) == "a cat"

    def test_plain_png_returns_none(self, tmp_path):
        path = _write(tmp_path, _png())
        assert extract_png_prompt(path) is None

    def test_not_a_png(self, tmp_path):
        p = tmp_path / "fake.png"
        p.write_bytes(b"GIF89a not a png at all")
        assert extract_png_prompt(p) is None

    def test_missing_file(self, tmp_path):
        assert extract_png_prompt(tmp_path / "absent.png") is None

    def test_truncated_chunk(self, tmp_path):
        # Declared length exceeds actual bytes — must not raise.
        bad = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 9999) + b"tEXtparam"
        p = tmp_path / "trunc.png"
        p.write_bytes(bad)
        assert extract_png_prompt(p) is None

    def test_zip_bomb_guarded(self, tmp_path):
        bomb = b"parameters\x00\x00" + zlib.compress(b"A" * (16 * 1024 * 1024))
        path = _write(tmp_path, _png(_chunk(b"zTXt", bomb)))
        assert extract_png_prompt(path) is None
