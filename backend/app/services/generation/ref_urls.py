"""Reference URLs the user's daemon may fetch.

The daemon downloads a job's references itself, over the public API, and only
from nous' own host (spec §10 SSRF) - so a reference has to leave the server
as an ABSOLUTE URL. Every caller that routes a generation to the daemon needs
this (the canvas, the shot video, the timeline); it lives here once so the
host they mint is the host ``classify_reference_url`` recognises.

What a caller is ALLOWED to reference is not decided here: that is each
caller's own scope rule (the canvas checks resource refs against the canvas's
scope; the shot and timeline only ever reference generated-media rows the
server itself wrote). This module only turns an already-admitted durable path
into a fetchable URL.
"""

from __future__ import annotations

from typing import Optional


def absolute_media_url(url: str) -> str:
    """Absolutise a relative durable url against the public API base."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    from app.core.config import settings

    # The fallback is imported, not spelled again: ``_own_hosts`` has to
    # RECOGNISE what this function MINTS, and two copies of the default host is
    # exactly how a URL we made ourselves ends up classified as foreign.
    from app.services.library.generated_media_service import (
        _DEFAULT_PUBLIC_API_BASE,
    )

    base = str(getattr(settings, "PUBLIC_API_BASE", "") or _DEFAULT_PUBLIC_API_BASE)
    return f"{base.rstrip('/')}{url}"


def generated_media_ref_url(url: Optional[str]) -> Optional[str]:
    """The absolute URL of a same-origin generated-media reference, else None.

    For callers whose reference is a ``/api/v1/generated-media/{id}/...`` path
    the server wrote itself (a shot's image, a timeline's tail frame). Anything
    else - a raw provider CDN url, a foreign host, a resource url that would
    need a scope check this caller does not have - is refused, which the caller
    treats exactly as its server path treats an unbridgeable image: no
    reference, text2video.
    """
    if not url:
        return None
    from app.services.library.generated_media_service import classify_reference_url

    kind, _row_id = classify_reference_url(str(url))
    if kind != "genmedia":
        return None
    return absolute_media_url(str(url))


__all__ = ["absolute_media_url", "generated_media_ref_url"]
