"""``codex exec`` takes ONE prompt: no system-prompt flag, no messages array.
Flatten the composed system message + history into labelled plain text.
Format is a contract (snapshot-tested) — the trailing instruction is what
keeps codex from treating the conversation as a coding task."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

_LABELS = {"user": "User", "assistant": "Assistant", "system": "System"}
_TAIL = (
    "Reply to the last user message directly, as plain text. "
    "Do not read or modify any files."
)


def _split_content(content: object) -> Tuple[str, List[str]]:
    """Return ``(text, image_urls)`` for either a bare string or the
    OpenAI multipart list. Unknown block types are dropped on purpose:
    codex takes text and image paths, nothing else."""
    if isinstance(content, str):
        return content, []
    texts: List[str] = []
    images: List[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "image_url":
            raw = block.get("image_url")
            url = raw.get("url") if isinstance(raw, dict) else None
            if isinstance(url, str) and url:
                images.append(url)
    return "\n".join(texts), images


def flatten_for_codex(
    system_message: str, messages: List[Dict[str, Any]]
) -> Tuple[str, List[str]]:
    parts: List[str] = []
    images: List[str] = []
    if (system_message or "").strip():
        parts.append(f"[System]\n{system_message.strip()}")
    lines: List[str] = []
    for m in messages:
        role = str(m.get("role") or "")
        if role == "tool":
            raise ValueError(
                "tool messages cannot reach codex-local: "
                "tools are rejected before the call"
            )
        text, imgs = _split_content(m.get("content"))
        images.extend(imgs)
        lines.append(f"{_LABELS.get(role, role.title())}: {text}")
    parts.append("[Conversation]\n" + "\n\n".join(lines))
    parts.append(_TAIL)
    return "\n\n".join(parts), images


__all__ = ["flatten_for_codex"]
