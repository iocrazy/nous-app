"""The gallery MIME type — the single source of truth on the backend.

A gallery is one ``resources`` row whose ``mime_type`` marks it as a gallery
entity (child images hang off ``gallery_items``). The value is PERSISTED, and
it is being renamed ``application/x-mediahub-gallery`` →
``application/x-nous-gallery`` in two steps, because migrations and code
deploy in no guaranteed order and old frontend bundles stay open:

1. (now) every read / filter accepts :data:`GALLERY_MIMES` (both values),
   but writes still use the LEGACY value (:data:`GALLERY_MIME_FOR_WRITE`), so
   a frontend bundle that predates this release never meets a value it does
   not recognise.
2. Once every deployed bundle accepts both, one PR flips
   :data:`GALLERY_MIME_FOR_WRITE` to :data:`GALLERY_MIME` AND ships the
   migration that rewrites existing rows. One release after that,
   :data:`LEGACY_GALLERY_MIME` can be dropped from :data:`GALLERY_MIMES`.

The frontend mirror lives in ``frontend/utils/galleryMime.ts``.
"""

from __future__ import annotations

GALLERY_MIME = "application/x-nous-gallery"
"""The target spelling (written once step 2 flips ``GALLERY_MIME_FOR_WRITE``)."""

LEGACY_GALLERY_MIME = "application/x-mediahub-gallery"
"""Pre-rename value still present on existing rows until the data migration."""

GALLERY_MIMES: tuple[str, ...] = (GALLERY_MIME, LEGACY_GALLERY_MIME)
"""Every value a read / filter must treat as a gallery."""

GALLERY_MIME_FOR_WRITE = LEGACY_GALLERY_MIME
"""The value written when a gallery is created.

Deliberately still the legacy spelling in step 1: old frontend bundles only
recognise it. Step 2 changes this line to ``GALLERY_MIME`` together with the
migration that rewrites existing rows.
"""


def is_gallery_mime(mime_type: str | None) -> bool:
    """True when ``mime_type`` marks a gallery entity (either spelling)."""
    return mime_type in GALLERY_MIMES
