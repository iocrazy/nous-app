"""Content items for one embedding, and the two wire shapes that carry them.

A *group* of content items (text, images, video) becomes ONE vector: the
provider fuses every item of a request into a single embedding. Pure
functions only — no I/O, no config — so both shapes are testable byte for
byte and ``EmbeddingService`` only chooses which one to send.

Wire shapes:

* **Ark multimodal** (Volcengine ``/embeddings/multimodal``)::

      {"model": m, "input": [{"type": "text", "text": ...},
                             {"type": "image_url", "image_url": {"url": ...}},
                             {"type": "video_url", "video_url": {"url": ...}}]}

  response vector at ``data.embedding`` (dict) or ``data[0].embedding``.
  Ark has no frame-list item, so :class:`VideoFramesItem` is sent as one
  ``image_url`` part per frame (still one fused vector).

* **OpenAI-compatible multimodal** (nous-engine ``/v1/embeddings``, see
  ``docs/superpowers/specs/2026-09-16-nous-engine-multimodal-embedding-request.md``
  §1): ``input`` is a list of groups, each group a list of parts; adds
  ``video_frames``; response ``data[i].embedding`` ordered by ``index``.

* **OpenAI chat-messages** (WeMM on nous-engine ``/v1/embeddings``)::

      {"model": m, "messages": [{"role": "user",
                                 "content": [{"type": "text", "text": ...}]}],
       "encoding_format": "float"}

  the gateway applies the model's chat template only to ``messages``;
  response ``data[0].embedding``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence, Union

Modality = Literal["text", "image", "video"]


@dataclass(frozen=True)
class TextItem:
    text: str


@dataclass(frozen=True)
class ImageUrlItem:
    """``url`` is an http(s) URL or a ``data:image/...;base64,`` URI."""

    url: str


@dataclass(frozen=True)
class VideoFramesItem:
    """Frames sampled by the caller (in order); one fused vector."""

    frame_urls: tuple[str, ...]


@dataclass(frozen=True)
class VideoUrlItem:
    """A whole video; the provider samples frames itself."""

    url: str


ContentItem = Union[TextItem, ImageUrlItem, VideoFramesItem, VideoUrlItem]


def modality_of(item: ContentItem) -> Modality:
    if isinstance(item, TextItem):
        return "text"
    if isinstance(item, ImageUrlItem):
        return "image"
    if isinstance(item, (VideoFramesItem, VideoUrlItem)):
        return "video"
    raise TypeError(f"not a content item: {type(item).__name__}")


def _text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _image_part(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def _video_url_part(url: str) -> dict[str, Any]:
    return {"type": "video_url", "video_url": {"url": url}}


def _ark_parts(item: ContentItem) -> list[dict[str, Any]]:
    if isinstance(item, TextItem):
        return [_text_part(item.text)]
    if isinstance(item, ImageUrlItem):
        return [_image_part(item.url)]
    if isinstance(item, VideoFramesItem):
        return [_image_part(u) for u in item.frame_urls]
    if isinstance(item, VideoUrlItem):
        return [_video_url_part(item.url)]
    raise TypeError(f"not a content item: {type(item).__name__}")


def build_ark_payload(model: str, items: Sequence[ContentItem]) -> dict[str, Any]:
    """Ark ``/embeddings/multimodal`` request body: all items → one vector."""
    return {
        "model": model,
        "input": [part for item in items for part in _ark_parts(item)],
    }


def parse_ark_response(body: dict[str, Any] | None) -> list[float] | None:
    """Vector from an Ark response, or ``None`` when the body carries none."""
    data = (body or {}).get("data") if isinstance(body, dict) else None
    emb = None
    if isinstance(data, dict):
        emb = data.get("embedding")
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        emb = data[0].get("embedding")
    if isinstance(emb, list) and emb:
        return [float(x) for x in emb]
    return None


def _openai_part(item: ContentItem) -> dict[str, Any]:
    if isinstance(item, TextItem):
        return _text_part(item.text)
    if isinstance(item, ImageUrlItem):
        return _image_part(item.url)
    if isinstance(item, VideoFramesItem):
        return {
            "type": "video_frames",
            "frames": [{"image_url": {"url": u}} for u in item.frame_urls],
        }
    if isinstance(item, VideoUrlItem):
        return _video_url_part(item.url)
    raise TypeError(f"not a content item: {type(item).__name__}")


def build_openai_multimodal_payload(
    model: str, groups: Sequence[Sequence[ContentItem]], dims: int
) -> dict[str, Any]:
    """OpenAI-compatible multimodal ``/v1/embeddings`` body: one vector per
    group. ``dims`` asks the engine for a matryoshka width (the columns')."""
    return {
        "model": model,
        "input": [[_openai_part(item) for item in group] for group in groups],
        "dimensions": dims,
        "encoding_format": "float",
    }


def build_openai_chat_payload(model: str, text: str) -> dict[str, Any]:
    """Chat-messages ``/v1/embeddings`` body: ``text`` as one user message.
    No ``input`` key — its presence makes the gateway skip the template."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": [_text_part(text)]}],
        "encoding_format": "float",
    }


def parse_openai_embeddings_response(
    body: dict[str, Any], expected: int
) -> list[list[float]]:
    """Vectors in input order (by ``index``). Raises ``ValueError`` when the
    count differs from ``expected`` or an entry has no vector — a partial
    answer must never be zipped against the wrong inputs."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ValueError("embeddings response has no data list")
    if len(data) != expected:
        raise ValueError(
            f"embeddings response has {len(data)} vectors, expected {expected}"
        )
    ordered = sorted(
        data, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0
    )
    out: list[list[float]] = []
    for pos, entry in enumerate(ordered):
        emb = entry.get("embedding") if isinstance(entry, dict) else None
        if not isinstance(emb, list) or not emb:
            raise ValueError(f"embeddings response entry {pos} has no vector")
        out.append([float(x) for x in emb])
    return out


__all__ = [
    "ContentItem",
    "ImageUrlItem",
    "Modality",
    "TextItem",
    "VideoFramesItem",
    "VideoUrlItem",
    "build_ark_payload",
    "build_openai_chat_payload",
    "build_openai_multimodal_payload",
    "modality_of",
    "parse_ark_response",
    "parse_openai_embeddings_response",
]
