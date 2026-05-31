"""Map a MIME type to a canonical resource kind used by the @-reference
picker, prompt composer, and ResourceFetch tool. Single source of truth
so adding a new kind only requires one edit."""

from __future__ import annotations


def kind_from_mime(mime: str | None) -> str:
    """Canonical kind for a resource based on its MIME type.

    Returns one of: ``video``, ``image``, ``audio``, ``pdf``, ``doc``
    (the last one is the catch-all for any text-like resource).
    """
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    if m.startswith("image/"):
        return "image"
    if m.startswith("audio/"):
        return "audio"
    if m == "application/pdf":
        return "pdf"
    return "doc"
