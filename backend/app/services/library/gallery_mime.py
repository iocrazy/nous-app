"""The gallery MIME type — the single source of truth on the backend.

A gallery is one ``resources`` row whose ``mime_type`` marks it as a gallery
entity (child images hang off ``gallery_items``). The value is PERSISTED, and
it is being renamed ``application/x-mediahub-gallery`` →
``application/x-nous-gallery`` in steps, because migrations and code
deploy in no guaranteed order and old frontend bundles stay open:

1. (#2388, shipped) every read / filter accepts :data:`GALLERY_MIMES` (both
   values), while writes stayed on the legacy value so a frontend bundle
   that predated that release never met a value it did not recognise.
2. (now) :data:`GALLERY_MIME_FOR_WRITE` is :data:`GALLERY_MIME`, shipped in
   the same PR as migration 488, which rewrites the existing rows. Reads
   still accept both — a row written by a backend that predates this release
   in the migration/deploy window keeps working either way.
3. (later) drop :data:`LEGACY_GALLERY_MIME` from :data:`GALLERY_MIMES` once
   step 2 has settled and no legacy row can be written any more.

The frontend mirror lives in ``frontend/utils/galleryMime.ts``.
"""

from __future__ import annotations

GALLERY_MIME = "application/x-nous-gallery"
"""The current spelling — what new galleries are written with."""

LEGACY_GALLERY_MIME = "application/x-mediahub-gallery"
"""Pre-rename value. Migration 488 rewrites existing rows; reads keep accepting
it until the step-3 cleanup."""

GALLERY_MIMES: tuple[str, ...] = (GALLERY_MIME, LEGACY_GALLERY_MIME)
"""Every value a read / filter must treat as a gallery."""

GALLERY_MIME_FOR_WRITE = GALLERY_MIME
"""The value written when a gallery is created.

The new spelling since step 2: every deployed frontend bundle accepts both
(#2388), and migration 488 moves the existing rows over in the same PR.
"""


def is_gallery_mime(mime_type: str | None) -> bool:
    """True when ``mime_type`` marks a gallery entity (either spelling)."""
    return mime_type in GALLERY_MIMES
