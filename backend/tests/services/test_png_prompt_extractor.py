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
    PngPromptPair,
    extract_comfyui_prompt,
    extract_png_prompt,
    extract_png_prompt_pair,
    parse_a1111_pair,
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


def _png_with_text_chunk(keyword: str, text: str) -> bytes:
    """Helper to build a PNG with a single tEXt chunk (keyword + null + text)."""
    data = keyword.encode("latin-1") + b"\x00" + text.encode("latin-1")
    return _png(_chunk(b"tEXt", data))


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


NO_NEG_BLOB = (
    "a cat sitting on a windowsill\n"
    "Steps: 30, Sampler: DPM++ 2M, CFG scale: 5, Seed: 42"
)

MULTILINE_NEG_BLOB = (
    "portrait, dramatic light\n"
    "Negative prompt: lowres, bad hands,\nextra fingers, watermark\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 7"
)


class TestParseA1111Pair:
    def test_pair_extracts_positive_and_negative(self):
        pair = parse_a1111_pair(A1111_BLOB)
        assert pair == PngPromptPair(
            positive="masterpiece, 1girl, silver hair,\nbacklit, golden hour",
            negative="lowres, bad anatomy",
        )

    def test_pair_without_negative(self):
        pair = parse_a1111_pair(NO_NEG_BLOB)
        assert pair.positive == "a cat sitting on a windowsill"
        assert pair.negative is None

    def test_multiline_negative_stops_at_settings_line(self):
        pair = parse_a1111_pair(MULTILINE_NEG_BLOB)
        assert pair.negative == "lowres, bad hands,\nextra fingers, watermark"

    def test_empty_blob_returns_none(self):
        assert parse_a1111_pair("") is None

    def test_legacy_positive_only_helper_unchanged(self):
        # back-compat: old callers still get the bare positive string
        assert parse_a1111_parameters(A1111_BLOB) == (
            "masterpiece, 1girl, silver hair,\nbacklit, golden hour"
        )


class TestExtractPngPromptPair:
    def test_pair_from_a1111_png(self, tmp_path: Path):
        png = tmp_path / "a.png"
        png.write_bytes(_png_with_text_chunk("parameters", A1111_BLOB))
        pair = extract_png_prompt_pair(png)
        assert pair.negative == "lowres, bad anatomy"

    def test_comfyui_png_has_no_negative(self, tmp_path: Path):
        graph = json.dumps(
            {
                "1": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "a long descriptive positive prompt here"},
                },
            }
        )
        png = tmp_path / "c.png"
        png.write_bytes(_png_with_text_chunk("prompt", graph))
        pair = extract_png_prompt_pair(png)
        assert pair.positive.startswith("a long descriptive")
        assert pair.negative is None

    def test_legacy_extract_still_positive_only(self, tmp_path: Path):
        png = tmp_path / "b.png"
        png.write_bytes(_png_with_text_chunk("parameters", A1111_BLOB))
        assert extract_png_prompt(png) == (
            "masterpiece, 1girl, silver hair,\nbacklit, golden hour"
        )


# ── Generation parameters (gen_params, migration 440) ─────────────────

from app.services.library.png_prompt_extractor import (  # noqa: E402
    extract_comfyui_generation,
    extract_png_generation,
    parse_a1111_settings,
)

A1111_FULL_BLOB = (
    "masterpiece, 1girl, <lora:detail_slider:0.6>, silver hair <lora:hanfu_v2:1>\n"
    "Negative prompt: lowres, bad anatomy\n"
    "Steps: 28, Sampler: DPM++ 2M, Schedule type: Karras, CFG scale: 5.5, "
    "Seed: 987654321, Size: 832x1216, Model hash: abcd1234, "
    "Model: sdxl\\animagine-xl-3.1.safetensors, Denoising strength: 0.4, "
    'Lora hashes: "detail_slider: 1111, hanfu_v2: 2222", Version: v1.9.4'
)

# Mirrors the real production PNG that motivated this feature (resource
# 342622656467658): Krea2 UNET, qwen3vl text encoder, seed wired from an
# rgthree Seed node, and a negative CLIPTextEncode that is NOT wired into
# the sampler (negative goes through ConditioningZeroOut instead).
COMFY_REAL_GRAPH = {
    "137": {
        "inputs": {"samples": ["158", 0], "vae": ["167", 0]},
        "class_type": "VAEDecode",
    },
    "140": {
        "inputs": {"conditioning": ["164", 0]},
        "class_type": "ConditioningZeroOut",
    },
    "153": {"inputs": {"seed": 399257458555967}, "class_type": "Seed (rgthree)"},
    "156": {
        "inputs": {"width": 1920, "height": 1080, "batch_size": 1},
        "class_type": "EmptyLatentImage",
    },
    "158": {
        "inputs": {
            "seed": ["153", 0],
            "steps": 10,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "model": ["168", 0],
            "positive": ["164", 0],
            "negative": ["140", 0],
            "latent_image": ["156", 0],
        },
        "class_type": "KSampler",
    },
    "164": {
        "inputs": {
            "text": "A realistic, intimate home photograph of a young woman",
            "clip": ["166", 0],
        },
        "class_type": "CLIPTextEncode",
    },
    "166": {
        "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2"},
        "class_type": "CLIPLoader",
    },
    "167": {
        "inputs": {"vae_name": "qwen_image_vae.safetensors"},
        "class_type": "VAELoader",
    },
    "168": {
        "inputs": {
            "unet_name": "Krea2\\Krea2-redcraft_fp8.safetensors",
            "weight_dtype": "default",
        },
        "class_type": "UNETLoader",
    },
    "173": {
        "inputs": {
            "text": "mosaic, censored, lowres, watermark, extra limbs, bad hands, jpeg artifacts, ugly",
            "clip": ["166", 0],
        },
        "class_type": "CLIPTextEncode",
    },
}


class TestParseA1111Settings:
    def test_full_settings_line(self):
        params = parse_a1111_settings(A1111_FULL_BLOB)
        assert params == {
            "tool": "a1111",
            "model": "animagine-xl-3.1",
            "model_hash": "abcd1234",
            "sampler": "DPM++ 2M",
            "scheduler": "Karras",
            "steps": 28,
            "cfg": 5.5,
            "seed": 987654321,
            "denoise": 0.4,
            "width": 832,
            "height": 1216,
            "loras": ["detail_slider", "hanfu_v2"],
        }

    def test_no_settings_line_returns_none(self):
        assert parse_a1111_settings("just a prompt\nNegative prompt: x") is None

    def test_minimal_line_omits_unknown_keys(self):
        params = parse_a1111_settings("p\nSteps: 20, Seed: 7")
        assert params == {"tool": "a1111", "steps": 20, "seed": 7}


class TestExtractComfyuiGeneration:
    def test_real_graph_follows_sampler_wiring(self):
        gen = extract_comfyui_generation(json.dumps(COMFY_REAL_GRAPH))
        assert gen.positive.startswith("A realistic, intimate")
        # #173 is longer than the positive but NOT wired into the sampler:
        # the wiring, not text length, decides — and an unwired negative
        # is reported as absent.
        assert gen.negative is None
        assert gen.params == {
            "tool": "comfyui",
            "model": "Krea2-redcraft_fp8",
            "sampler": "euler",
            "scheduler": "simple",
            "steps": 10,
            "cfg": 1.0,
            "denoise": 1.0,
            "seed": 399257458555967,
            "width": 1920,
            "height": 1080,
            "text_encoder": "qwen3vl_4b_fp8_scaled",
            "vae": "qwen_image_vae",
        }

    def test_wired_negative_and_lora_chain(self):
        graph = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd15/dreamshaper_8.safetensors"},
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "add_detail.safetensors", "model": ["1", 0]},
            },
            "3": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"lora_name": "styles\\ink.safetensors", "model": ["2", 0]},
            },
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat"}},
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "a very long negative prompt, lowres, blurry, bad"},
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 768},
            },
            "7": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "noise_seed": 42,
                    "steps": 30,
                    "cfg": 7,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "model": ["3", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                },
            },
        }
        gen = extract_comfyui_generation(json.dumps(graph))
        # Short positive wins over the longer negative because of wiring.
        assert gen.positive == "cat"
        assert gen.negative.startswith("a very long negative")
        assert gen.params["model"] == "dreamshaper_8"
        assert gen.params["loras"] == ["add_detail", "ink"]
        assert gen.params["seed"] == 42
        assert (gen.params["width"], gen.params["height"]) == (512, 768)
        assert gen.params["cfg"] == 7.0

    def test_no_sampler_falls_back_to_longest_text(self):
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "only prompt here"},
            }
        }
        gen = extract_comfyui_generation(json.dumps(graph))
        assert gen.positive == "only prompt here"
        assert gen.negative is None
        assert gen.params is None

    def test_cyclic_graph_does_not_hang(self):
        graph = {
            "1": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["1", 0]},
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "x", "model": ["2", 0]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "model": ["2", 0], "steps": 1},
            },
        }
        assert extract_comfyui_generation(json.dumps(graph)) is None


class TestExtractPngGeneration:
    def test_a1111_png_yields_prompts_and_params(self, tmp_path: Path):
        png = tmp_path / "a.png"
        png.write_bytes(_png_with_text_chunk("parameters", A1111_FULL_BLOB))
        gen = extract_png_generation(png)
        assert gen.negative == "lowres, bad anatomy"
        assert gen.params["steps"] == 28
        assert gen.params["loras"] == ["detail_slider", "hanfu_v2"]

    def test_comfy_png_yields_params(self, tmp_path: Path):
        png = tmp_path / "c.png"
        png.write_bytes(_png_with_text_chunk("prompt", json.dumps(COMFY_REAL_GRAPH)))
        gen = extract_png_generation(png)
        assert gen.params["seed"] == 399257458555967
        # The prompt-only view stays consistent with the full one.
        pair = extract_png_prompt_pair(png)
        assert (pair.positive, pair.negative) == (gen.positive, gen.negative)

    def test_plain_png_returns_none(self, tmp_path: Path):
        png = tmp_path / "p.png"
        png.write_bytes(_png())
        assert extract_png_generation(png) is None
