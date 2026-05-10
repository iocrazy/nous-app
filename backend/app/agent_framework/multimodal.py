"""Multi-modal message construction — image / video / PDF inputs.

Phase Q (Q1). Today the chat path accepts only text. Modern LLMs
(GPT-4o, Claude 4, Qwen-VL) support multi-modal: image_url, base64
image_data, etc. Frontend sometimes wants to attach a screenshot, a
PDF page, a video thumbnail.

This module gives the value-object + builder that converts user-side
attachments into the OpenAI-compatible multi-part message shape that
adapters expect:

    {"role": "user", "content": [
        {"type": "text", "text": "look at this:"},
        {"type": "image_url", "image_url": {"url": "https://..."}},
    ]}

Adapters that don't support multi-modal (or providers without vision)
gracefully degrade: ``flatten_to_text`` strips non-text parts +
inserts a "[image:url]" placeholder so the model sees SOMETHING about
the attachment.

Pure layer:
  - Attachment dataclass (kind + url/data_url + mime + alt_text)
  - build_user_message(text, attachments) → multi-part dict
  - flatten_to_text(message) → str (degrade for text-only adapters)
  - sniff_supports_vision(model) → bool (cheap heuristic)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AttachmentKind(str, Enum):
    IMAGE = "image"
    VIDEO_THUMBNAIL = "video_thumbnail"  # treated as image to model
    PDF_PAGE = "pdf_page"  # rendered to image first by caller
    AUDIO = "audio"  # OpenAI/Qwen audio input (rare)


# Common content-type sniff for image vs other
_IMAGE_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
        "image/bmp",
    }
)


@dataclass(frozen=True)
class Attachment:
    """One user-supplied non-text input."""

    kind: AttachmentKind
    url: str = ""  # public/signed URL
    data_url: str = ""  # base64 data: URL (use when no public URL)
    mime: str = ""  # explicit mime type
    alt_text: str = ""  # caption / fallback for text-only adapters

    def is_inline(self) -> bool:
        return bool(self.data_url)

    def display_url(self) -> str:
        """Use whichever URL form is set; prefer URL over data_url
        (URLs are smaller in the message)."""
        return self.url or self.data_url


# Models known to support image input. Conservative heuristic — when
# in doubt, flatten_to_text degrades the input (UX still works).
_VISION_MODEL_PREFIXES = (
    "gpt-4o",
    "gpt-4-vision",
    "gpt-4-turbo",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "gemini-",
    "doubao-vision",
)


def sniff_supports_vision(model: str) -> bool:
    if not model:
        return False
    m = model.lower()
    return any(m.startswith(p) for p in _VISION_MODEL_PREFIXES)


def build_user_message(
    text: str,
    attachments: list[Attachment] | None = None,
    *,
    target_model: str = "",
) -> dict[str, Any]:
    """Construct the user-message dict.

    If no attachments OR the model isn't vision-capable, returns plain
    {"role": "user", "content": "<text + flattened captions>"} so a
    text-only adapter accepts it.

    Otherwise returns the OpenAI multi-part shape with image_url parts.
    """
    atts = list(attachments or [])
    if not atts:
        return {"role": "user", "content": text or ""}

    if not sniff_supports_vision(target_model):
        # Degrade: inline image references as text placeholders so the
        # model at least knows attachments exist
        return {
            "role": "user",
            "content": flatten_attachments_to_text(text, atts),
        }

    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for att in atts:
        url = att.display_url()
        if not url:
            continue
        if att.kind in {
            AttachmentKind.IMAGE,
            AttachmentKind.VIDEO_THUMBNAIL,
            AttachmentKind.PDF_PAGE,
        }:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                }
            )
        elif att.kind == AttachmentKind.AUDIO:
            # Per OpenAI 2024-10 audio support — rarer + provider specific
            parts.append(
                {
                    "type": "input_audio",
                    "input_audio": {"data": url, "format": att.mime or "wav"},
                }
            )
    return {"role": "user", "content": parts}


def flatten_attachments_to_text(text: str, attachments: list[Attachment]) -> str:
    """Convert attachments → text placeholders for text-only adapters.

    Each attachment becomes "[image: <alt or url>]" line. Original
    text appended. The model gets enough signal to acknowledge that
    attachments existed even though it can't see them.
    """
    lines = []
    for i, att in enumerate(attachments, start=1):
        label = att.alt_text or att.url or "(inline data)"
        kind_word = att.kind.value.replace("_", " ")
        lines.append(f"[attachment {i} — {kind_word}: {label[:200]}]")
    if text:
        lines.append("")
        lines.append(text)
    return "\n".join(lines)


def flatten_to_text(message: dict) -> str:
    """Convert a (possibly multi-part) message back to plain text.
    Used when downstream component (compactor / harvester) doesn't
    support multi-part content."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            out.append(str(part))
            continue
        ptype = part.get("type")
        if ptype == "text":
            out.append(str(part.get("text") or ""))
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            out.append(f"[image: {url[:200]}]")
        elif ptype == "input_audio":
            out.append("[audio]")
        else:
            out.append(f"[{ptype or 'attachment'}]")
    return "\n".join(out)


_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,")


def looks_like_data_url(s: str) -> bool:
    return bool(s) and bool(_DATA_URL_RE.match(s))


__all__ = [
    "Attachment",
    "AttachmentKind",
    "build_user_message",
    "flatten_attachments_to_text",
    "flatten_to_text",
    "looks_like_data_url",
    "sniff_supports_vision",
]
