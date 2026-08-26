# backend/app/services/library/png_prompt_extractor.py

"""Extract AI generation prompts embedded in PNG metadata.

AI image tools persist the generation prompt inside PNG text chunks:

- **A1111 / SD WebUI (and Forge/derivatives)** write a ``parameters``
  tEXt/iTXt chunk: positive prompt, then ``Negative prompt: ...``, then a
  ``Steps: ..., Sampler: ...`` settings line.
- **ComfyUI** writes a ``prompt`` tEXt chunk holding the API-format graph
  JSON; the human-readable prompt lives in ``CLIPTextEncode`` node inputs.

This module parses the chunks with the stdlib only (struct/zlib/json) — no
Pillow dependency for the text path, and bounded reads so a hostile file
can't balloon memory (uploads are untrusted input).

Used by ``upload_postprocess_workflow`` to auto-fill ``resources.gen_prompt``
on PNG upload when the user hasn't entered one (IC-port P1, 2026-06-12).

Since 2026-08-26 the same pass also yields the generation PARAMETERS
(model / sampler / scheduler / steps / cfg / seed / size / loras …) as one
normalised dict — ``extract_png_generation`` — which lands in
``resources.gen_params`` (migration 440). The dict shape is shared with
``promote_generated_media`` so the detail panel renders every source alike.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional, Tuple

from loguru import logger

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Text chunks carrying prompts are small (a few KB); anything beyond this is
# either corrupt or hostile — skip it rather than buffer it.
MAX_TEXT_CHUNK_BYTES = 8 * 1024 * 1024
# Cap decompressed zTXt/iTXt payloads (zip-bomb guard).
MAX_DECOMPRESSED_BYTES = 4 * 1024 * 1024
# Mirror the ResourceUpdate.gen_prompt schema cap.
MAX_PROMPT_CHARS = 20000

_TEXT_CHUNK_TYPES = (b"tEXt", b"iTXt", b"zTXt")
_SETTINGS_LINE_RE = re.compile(r"^Steps: \d")


def _bounded_decompress(data: bytes) -> Optional[bytes]:
    """zlib-inflate with a hard output cap; None on overflow or bad data."""
    try:
        obj = zlib.decompressobj()
        out = obj.decompress(data, MAX_DECOMPRESSED_BYTES)
        if obj.unconsumed_tail:
            return None
        return out
    except zlib.error:
        return None


def _decode_text_chunk(chunk_type: bytes, data: bytes) -> Optional[Tuple[str, str]]:
    """Decode one tEXt/iTXt/zTXt chunk into ``(keyword, text)``."""
    try:
        if chunk_type == b"tEXt":
            keyword, _, text = data.partition(b"\x00")
            return keyword.decode("latin-1"), text.decode("latin-1")

        if chunk_type == b"zTXt":
            keyword, _, rest = data.partition(b"\x00")
            if not rest:  # missing compression-method byte
                return None
            inflated = _bounded_decompress(rest[1:])
            if inflated is None:
                return None
            return keyword.decode("latin-1"), inflated.decode(
                "latin-1", errors="replace"
            )

        if chunk_type == b"iTXt":
            keyword, _, rest = data.partition(b"\x00")
            if len(rest) < 2:
                return None
            compressed = rest[0] == 1
            # Skip compression flag + method, then language tag and
            # translated keyword (both null-terminated).
            rest = rest[2:]
            _, _, rest = rest.partition(b"\x00")
            _, _, rest = rest.partition(b"\x00")
            if compressed:
                inflated = _bounded_decompress(rest)
                if inflated is None:
                    return None
                rest = inflated
            return keyword.decode("latin-1"), rest.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, ValueError):
        return None
    return None


def _iter_text_chunks(path: Path) -> Iterator[Tuple[str, str]]:
    """Yield ``(keyword, text)`` for every text chunk, skipping bulk data."""
    with path.open("rb") as f:
        if f.read(8) != PNG_SIGNATURE:
            return
        while True:
            header = f.read(8)
            if len(header) < 8:
                return
            (length,) = struct.unpack(">I", header[:4])
            chunk_type = header[4:]
            if chunk_type == b"IEND":
                return
            if chunk_type in _TEXT_CHUNK_TYPES and length <= MAX_TEXT_CHUNK_BYTES:
                data = f.read(length)
                if len(data) < length:
                    return
                f.seek(4, 1)  # CRC
                decoded = _decode_text_chunk(chunk_type, data)
                if decoded:
                    yield decoded
            else:
                # IDAT and friends: skip without buffering.
                f.seek(length + 4, 1)


class PngPromptPair(NamedTuple):
    """Positive + optional negative prompt extracted from PNG metadata."""

    positive: str
    negative: Optional[str]


def parse_a1111_pair(text: str) -> Optional[PngPromptPair]:
    """Split an A1111 ``parameters`` blob into positive/negative prompts.

    Blob layout: positive (multi-line) → optional ``Negative prompt: ...``
    block (multi-line) → ``Steps: ...`` settings line. ComfyUI graphs don't
    label negative in API format, so this only applies to A1111 blobs.
    """
    pos_lines: list[str] = []
    neg_lines: list[str] = []
    section = "positive"
    for line in text.splitlines():
        if section == "positive":
            if line.startswith("Negative prompt:"):
                section = "negative"
                first = line[len("Negative prompt:") :].strip()
                if first:
                    neg_lines.append(first)
                continue
            if _SETTINGS_LINE_RE.match(line):
                break
            pos_lines.append(line)
        else:
            if _SETTINGS_LINE_RE.match(line):
                break
            neg_lines.append(line)
    positive = "\n".join(pos_lines).strip()
    if not positive:
        return None
    negative = "\n".join(neg_lines).strip() or None
    return PngPromptPair(positive=positive, negative=negative)


def parse_a1111_parameters(text: str) -> str:
    """Extract the positive prompt from an A1111 ``parameters`` blob."""
    pair = parse_a1111_pair(text)
    return pair.positive if pair else ""


def extract_comfyui_prompt(graph_json: str) -> Optional[str]:
    """Pull the most likely positive prompt out of a ComfyUI API graph.

    The graph maps node ids to ``{class_type, inputs}``. Prompt text lives
    in ``CLIPTextEncode``-family nodes' ``inputs.text``. Positive vs
    negative isn't labeled in API format, so use the longest text — the
    positive prompt is, in practice, the descriptive (longer) one.
    """
    try:
        graph = json.loads(graph_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(graph, dict):
        return None

    texts: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if "CLIPTextEncode" not in str(node.get("class_type", "")):
            continue
        text = (node.get("inputs") or {}).get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    if not texts:
        return None
    return max(texts, key=len)


def extract_png_prompt_pair(file_path: str | Path) -> Optional[PngPromptPair]:
    """Positive+negative generation prompts from a PNG, or None.

    Thin view over ``extract_png_generation`` (same precedence, same
    never-raises contract) for callers that only want the prompts.
    """
    gen = extract_png_generation(file_path)
    if not gen:
        return None
    return PngPromptPair(positive=gen.positive, negative=gen.negative)


def extract_png_prompt(file_path: str | Path) -> Optional[str]:
    """Back-compat: positive prompt only. See extract_png_prompt_pair."""
    pair = extract_png_prompt_pair(file_path)
    return pair.positive if pair else None


# ── Generation parameters ──────────────────────────────────────────────

# Cap on any single string value stored into gen_params (model names,
# lora names) and on the lora list — the column is for display, not for
# round-tripping a whole graph.
MAX_PARAM_STR_CHARS = 200
MAX_LORAS = 20

# A1111 settings line: `Key: value, Key: value, Key: "quoted, value"`.
_A1111_KV_RE = re.compile(r'\s*([^:,]+?):\s*("(?:\\.|[^"])*"|[^,]*)(?:,|$)')
_A1111_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$")
_LORA_TAG_RE = re.compile(r"<lora:([^:>]+)(?::[^>]*)?>")

_COMFY_SAMPLER_TYPES = ("KSampler", "KSamplerAdvanced")
_COMFY_MODEL_LOADERS = {
    "UNETLoader": "unet_name",
    "CheckpointLoaderSimple": "ckpt_name",
    "CheckpointLoader": "ckpt_name",
    "UnetLoaderGGUF": "unet_name",
}
_COMFY_LATENT_TYPES = (
    "EmptyLatentImage",
    "EmptySD3LatentImage",
    "EmptyHunyuanLatentVideo",
)


class PngGeneration(NamedTuple):
    """Everything the PNG metadata tells us about how the image was made."""

    positive: str
    negative: Optional[str]
    params: Optional[dict]


def _clip_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:MAX_PARAM_STR_CHARS] if value else None


def _as_int(value: Any) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _put(params: dict, key: str, value: Any) -> None:
    """Set only when the value is meaningful — absent == unknown."""
    if value is None or value == "" or value == []:
        return
    params[key] = value


def _model_basename(name: Optional[str]) -> Optional[str]:
    """``Krea2\\Krea2-foo_fp8.safetensors`` → ``Krea2-foo_fp8``.

    Checkpoint names carry the loader's subfolder and extension; neither
    helps the reader identify the model.
    """
    if not name:
        return None
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"\.(safetensors|ckpt|pt|pth|gguf|bin)$", "", base, flags=re.I)
    return _clip_str(base)


def parse_a1111_settings(text: str) -> Optional[dict]:
    """Parse the ``Steps: …`` settings line of an A1111 blob into gen_params.

    Only the settings line is read (positive/negative are handled by
    ``parse_a1111_pair``); ``<lora:name:w>`` tags in the positive prompt
    are folded into ``loras``. None when there is no settings line.
    """
    settings_line: Optional[str] = None
    prompt_lines: list[str] = []
    for line in text.splitlines():
        if _SETTINGS_LINE_RE.match(line):
            settings_line = line
            break
        prompt_lines.append(line)
    if settings_line is None:
        return None

    kv: dict[str, str] = {}
    for m in _A1111_KV_RE.finditer(settings_line):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        if key and key not in kv:
            kv[key] = val

    params: dict[str, Any] = {"tool": "a1111"}
    _put(params, "model", _model_basename(kv.get("model")))
    _put(params, "model_hash", _clip_str(kv.get("model hash")))
    _put(params, "sampler", _clip_str(kv.get("sampler")))
    _put(params, "scheduler", _clip_str(kv.get("schedule type")))
    _put(params, "steps", _as_int(kv.get("steps")))
    _put(params, "cfg", _as_float(kv.get("cfg scale")))
    _put(params, "seed", _as_int(kv.get("seed")))
    _put(params, "denoise", _as_float(kv.get("denoising strength")))
    size = _A1111_SIZE_RE.match(kv.get("size", ""))
    if size:
        params["width"], params["height"] = int(size.group(1)), int(size.group(2))
    loras: list[str] = []
    for name in _LORA_TAG_RE.findall("\n".join(prompt_lines)):
        clipped = _clip_str(name)
        if clipped and clipped not in loras:
            loras.append(clipped)
    _put(params, "loras", loras[:MAX_LORAS])
    return params


def _comfy_link_target(graph: dict, ref: Any) -> Optional[dict]:
    """Resolve a ComfyUI API-format input link ``[node_id, output_idx]``."""
    if isinstance(ref, list) and ref and isinstance(ref[0], (str, int)):
        node = graph.get(str(ref[0]))
        return node if isinstance(node, dict) else None
    return None


def _comfy_text_at(graph: dict, ref: Any, depth: int = 0) -> Optional[str]:
    """Follow a conditioning link back to its CLIPTextEncode text.

    ``ConditioningZeroOut`` (and any other node without ``text``) means
    "no prompt on this side" — return None rather than guessing. Bounded
    depth guards against cyclic graphs in hostile files.
    """
    if depth > 8:
        return None
    node = _comfy_link_target(graph, ref)
    if node is None:
        return None
    inputs = node.get("inputs") or {}
    if "CLIPTextEncode" in str(node.get("class_type", "")):
        text = inputs.get("text")
        # The text itself may be wired from a primitive/string node.
        if isinstance(text, list):
            src = _comfy_link_target(graph, text)
            src_inputs = (src or {}).get("inputs") or {}
            for key in ("text", "string", "value"):
                if isinstance(src_inputs.get(key), str):
                    return src_inputs[key].strip() or None
            return None
        return text.strip() if isinstance(text, str) and text.strip() else None
    if str(node.get("class_type", "")) == "ConditioningZeroOut":
        return None
    # Pass-through conditioning nodes (ConditioningCombine, ControlNet
    # apply, FluxGuidance…): follow the first conditioning-looking input.
    for key in ("conditioning", "positive", "conditioning_1"):
        if key in inputs:
            return _comfy_text_at(graph, inputs[key], depth + 1)
    return None


def _comfy_model_chain(graph: dict, ref: Any) -> tuple[Optional[str], list[str]]:
    """Walk ``model`` links through LoRA loaders to the checkpoint/UNET."""
    loras: list[str] = []
    for _ in range(16):
        node = _comfy_link_target(graph, ref)
        if node is None:
            break
        ctype = str(node.get("class_type", ""))
        inputs = node.get("inputs") or {}
        if ctype in _COMFY_MODEL_LOADERS:
            # Walked sampler→checkpoint, i.e. last-applied LoRA first; report
            # them in the order the graph applies them.
            return _model_basename(inputs.get(_COMFY_MODEL_LOADERS[ctype])), loras[::-1]
        if "Lora" in ctype or "LoRA" in ctype:
            name = _model_basename(inputs.get("lora_name"))
            if name and name not in loras:
                loras.append(name)
        ref = inputs.get("model")
        if ref is None:
            break
    return None, loras[::-1]


def extract_comfyui_generation(graph_json: str) -> Optional[PngGeneration]:
    """Positive/negative prompts + params from a ComfyUI API-format graph.

    Prefers the sampler's actual wiring: ``KSampler.positive`` /
    ``.negative`` links tell positive from negative exactly (API format
    doesn't label them) and expose an unwired negative node as what it is —
    absent. Falls back to the longest CLIPTextEncode text when no sampler
    is found, so graphs from unknown sampler nodes still yield a prompt.
    """
    try:
        graph = json.loads(graph_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(graph, dict):
        return None

    sampler: Optional[dict] = None
    for node in graph.values():
        if (
            isinstance(node, dict)
            and str(node.get("class_type", "")) in _COMFY_SAMPLER_TYPES
        ):
            sampler = node
            break

    params: dict[str, Any] = {"tool": "comfyui"}
    positive: Optional[str] = None
    negative: Optional[str] = None
    if sampler is not None:
        inputs = sampler.get("inputs") or {}
        positive = _comfy_text_at(graph, inputs.get("positive"))
        negative = _comfy_text_at(graph, inputs.get("negative"))
        model, loras = _comfy_model_chain(graph, inputs.get("model"))
        _put(params, "model", model)
        _put(params, "loras", loras[:MAX_LORAS])
        _put(params, "sampler", _clip_str(inputs.get("sampler_name")))
        _put(params, "scheduler", _clip_str(inputs.get("scheduler")))
        _put(params, "steps", _as_int(inputs.get("steps")))
        _put(params, "cfg", _as_float(inputs.get("cfg")))
        _put(params, "denoise", _as_float(inputs.get("denoise")))
        seed_ref = inputs.get("seed", inputs.get("noise_seed"))
        if isinstance(seed_ref, list):  # wired from a seed node
            seed_node = _comfy_link_target(graph, seed_ref) or {}
            seed_ref = (seed_node.get("inputs") or {}).get("seed")
        _put(params, "seed", _as_int(seed_ref))
        latent = _comfy_link_target(graph, inputs.get("latent_image"))
        if latent and str(latent.get("class_type", "")) in _COMFY_LATENT_TYPES:
            li = latent.get("inputs") or {}
            _put(params, "width", _as_int(li.get("width")))
            _put(params, "height", _as_int(li.get("height")))

    # Text encoder / VAE aren't on the sampler's wiring — take the first of each.
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ctype = str(node.get("class_type", ""))
        ni = node.get("inputs") or {}
        if ctype in ("CLIPLoader", "DualCLIPLoader") and "text_encoder" not in params:
            _put(
                params,
                "text_encoder",
                _model_basename(ni.get("clip_name") or ni.get("clip_name1")),
            )
        elif ctype == "VAELoader" and "vae" not in params:
            _put(params, "vae", _model_basename(ni.get("vae_name")))

    if not positive:
        positive = extract_comfyui_prompt(graph_json)
        negative = None
    if not positive:
        return None
    return PngGeneration(
        positive=positive,
        negative=negative,
        params=params if len(params) > 1 else None,
    )


def extract_png_generation(file_path: str | Path) -> Optional[PngGeneration]:
    """Prompts + generation params from a PNG, or None. Never raises.

    Same precedence as ``extract_png_prompt_pair``: A1111 ``parameters``
    first, then the ComfyUI ``prompt`` graph.
    """
    path = Path(file_path)
    try:
        comfy_graph: Optional[str] = None
        for keyword, text in _iter_text_chunks(path):
            if keyword == "parameters":
                pair = parse_a1111_pair(text)
                if pair:
                    return PngGeneration(
                        positive=pair.positive[:MAX_PROMPT_CHARS],
                        negative=(
                            pair.negative[:MAX_PROMPT_CHARS] if pair.negative else None
                        ),
                        params=parse_a1111_settings(text),
                    )
            elif keyword == "prompt" and comfy_graph is None:
                comfy_graph = text
        if comfy_graph:
            gen = extract_comfyui_generation(comfy_graph)
            if gen:
                return PngGeneration(
                    positive=gen.positive[:MAX_PROMPT_CHARS],
                    negative=gen.negative[:MAX_PROMPT_CHARS] if gen.negative else None,
                    params=gen.params,
                )
        return None
    except OSError as e:
        logger.warning(f"[PngPrompt] cannot read {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[PngPrompt] unexpected parse failure for {path}: {e}")
        return None
