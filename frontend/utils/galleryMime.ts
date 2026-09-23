/**
 * The gallery MIME type — the single source of truth on the frontend.
 *
 * A gallery is one `resources` row whose `mime_type` marks it as a gallery
 * entity (its ordered child images hang off `gallery_items`). The value is
 * PERSISTED; it was renamed from the pre-rename `mediahub` spelling to
 * `application/x-nous-gallery` in three steps, because migrations and code
 * deploy in no guaranteed order:
 *
 *   1. (#2388) every check here accepted both spellings.
 *   2. (#2391) the backend flipped its write to `GALLERY_MIME` and migration
 *      488 rewrote the existing rows (production: zero legacy rows left).
 *   3. (now) the legacy spelling is no longer accepted — a row carrying it is
 *      not a gallery.
 *
 * The frontend never writes a gallery mime itself — `createGallery` only POSTs
 * a scope + filename and the backend picks the value.
 *
 * Mirror of `backend/app/services/library/gallery_mime.py`. Never compare a
 * mime against a gallery literal elsewhere — `galleryMime.test.ts` scans for it.
 */

/** The gallery MIME — what the backend writes and every check recognises. */
export const GALLERY_MIME = 'application/x-nous-gallery';

/** Every value that marks a gallery entity. A list (not a single compare) so a
 *  future rename can reuse the accept-both → flip-write → drop-legacy steps. */
export const GALLERY_MIMES: readonly string[] = [GALLERY_MIME];

/** True when `mimeType` marks a gallery entity. */
export function isGalleryMime(mimeType: string | null | undefined): boolean {
  return !!mimeType && GALLERY_MIMES.includes(mimeType);
}
