/**
 * The gallery MIME type — the single source of truth on the frontend.
 *
 * A gallery is one `resources` row whose `mime_type` marks it as a gallery
 * entity (its ordered child images hang off `gallery_items`). The value is
 * PERSISTED and is being renamed `application/x-mediahub-gallery` →
 * `application/x-nous-gallery` in two steps, because migrations and code
 * deploy in no guaranteed order:
 *
 *   1. (now) the backend writes `GALLERY_MIME`; every check here accepts
 *      `GALLERY_MIMES` (both spellings) via `isGalleryMime`.
 *   2. A migration rewrites existing rows; one release later the legacy
 *      spelling can be dropped from `GALLERY_MIMES`.
 *
 * Mirror of `backend/app/services/library/gallery_mime.py`. Never compare a
 * mime against a gallery literal elsewhere — `galleryMime.test.ts` scans for it.
 */

/** The spelling written for new galleries. */
export const GALLERY_MIME = 'application/x-nous-gallery';

/** Pre-rename spelling still on existing rows until the data migration. */
export const LEGACY_GALLERY_MIME = 'application/x-mediahub-gallery';

/** Every spelling that marks a gallery entity. */
export const GALLERY_MIMES: readonly string[] = [GALLERY_MIME, LEGACY_GALLERY_MIME];

/** True when `mimeType` marks a gallery entity (either spelling). */
export function isGalleryMime(mimeType: string | null | undefined): boolean {
  return !!mimeType && GALLERY_MIMES.includes(mimeType);
}
