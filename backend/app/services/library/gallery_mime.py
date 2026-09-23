"""The gallery MIME type — the single source of truth on the backend.

A gallery is one ``resources`` row whose ``mime_type`` marks it as a gallery
entity (child images hang off ``gallery_items``). The value is PERSISTED, and
it is being renamed ``application/x-mediahub-gallery`` →
``application/x-nous-gallery`` in two steps, because migrations and code
deploy in no guaranteed order and old frontend bundles stay open:

1. (this module) every write uses :data:`GALLERY_MIME` (the new value); every
   read / filter accepts :data:`GALLERY_MIMES` (both values).
2. A migration rewrites existing rows to the new value. One release after
   that, :data:`LEGACY_GALLERY_MIME` can be dropped from :data:`GALLERY_MIMES`.

The frontend mirror lives in ``frontend/utils/galleryMime.ts``.
"""

from __future__ import annotations

GALLERY_MIME = "application/x-nous-gallery"
"""The value written when a gallery is created."""

LEGACY_GALLERY_MIME = "application/x-mediahub-gallery"
"""Pre-rename value still present on existing rows until the data migration."""

GALLERY_MIMES: tuple[str, ...] = (GALLERY_MIME, LEGACY_GALLERY_MIME)
"""Every value a read / filter must treat as a gallery."""


def is_gallery_mime(mime_type: str | None) -> bool:
    """True when ``mime_type`` marks a gallery entity (either spelling)."""
    return mime_type in GALLERY_MIMES
