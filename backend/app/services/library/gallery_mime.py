"""The gallery MIME type — the single source of truth on the backend.

A gallery is one ``resources`` row whose ``mime_type`` marks it as a gallery
entity (child images hang off ``gallery_items``). The value is PERSISTED; it
was renamed ``application/x-mediahub-gallery`` → ``application/x-nous-gallery``
in three steps, because migrations and code deploy in no guaranteed order and
old frontend bundles stay open:

1. (#2388) every read / filter accepted both spellings; writes stayed legacy.
2. (#2391, with migration 488) writes flipped to the new spelling and the
   existing rows were rewritten.
3. (now) the legacy spelling is no longer accepted anywhere. Production held
   zero legacy rows after migration 488 and no deployed backend writes it any
   more, so a row carrying it is simply not a gallery.

The frontend mirror lives in ``frontend/utils/galleryMime.ts``.
"""

from __future__ import annotations

GALLERY_MIME = "application/x-nous-gallery"
"""The gallery MIME — what galleries are written with and recognised by."""

GALLERY_MIMES: tuple[str, ...] = (GALLERY_MIME,)
"""Every value a read / filter must treat as a gallery.

Kept as a collection (the resources repository binds it as
``ANY(:gallery_mimes)``) so a future rename can reuse the same
accept-both → flip-write → drop-legacy sequence without touching the SQL."""


def is_gallery_mime(mime_type: str | None) -> bool:
    """True when ``mime_type`` marks a gallery entity."""
    return mime_type in GALLERY_MIMES
