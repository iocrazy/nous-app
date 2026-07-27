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
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path
from typing import Iterator, NamedTuple, Optional, Tuple

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
    """Extract positive+negative generation prompts from a PNG, or None.

    A1111 ``parameters`` first (labeled negative), then ComfyUI ``prompt``
    graph (positive only — API format doesn't label negative). Never raises.
    """
    path = Path(file_path)
    try:
        comfy_graph: Optional[str] = None
        for keyword, text in _iter_text_chunks(path):
            if keyword == "parameters":
                pair = parse_a1111_pair(text)
                if pair:
                    return PngPromptPair(
                        positive=pair.positive[:MAX_PROMPT_CHARS],
                        negative=(
                            pair.negative[:MAX_PROMPT_CHARS] if pair.negative else None
                        ),
                    )
            elif keyword == "prompt" and comfy_graph is None:
                comfy_graph = text
        if comfy_graph:
            prompt = extract_comfyui_prompt(comfy_graph)
            if prompt:
                return PngPromptPair(positive=prompt[:MAX_PROMPT_CHARS], negative=None)
        return None
    except OSError as e:
        logger.warning(f"[PngPrompt] cannot read {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[PngPrompt] unexpected parse failure for {path}: {e}")
        return None


def extract_png_prompt(file_path: str | Path) -> Optional[str]:
    """Back-compat: positive prompt only. See extract_png_prompt_pair."""
    pair = extract_png_prompt_pair(file_path)
    return pair.positive if pair else None
