// frontend/components/resources/fileArrowNav.ts
import { GALLERY_MIME } from '../../services/resourceService';

/**
 * The resource detail page binds ← / → to previous / next FILE navigation. But
 * some media own those keys themselves and must win:
 *   - video/*  → VideoPlayer uses arrows to seek.
 *   - gallery  → GalleryViewer uses arrows to page through child images.
 *
 * Both the detail page and those players attach bubble-phase `window` keydown
 * listeners, so `preventDefault` alone does not stop the file-nav handler from
 * also firing. The clean fix is for the file-nav handler to bow out entirely
 * for these mime types. Returns true when file-level ← / → nav must be
 * suppressed (so the media's own pager/seek handler is the only one that acts).
 */
export function shouldSuppressFileArrowNav(
  mimeType: string | null | undefined,
): boolean {
  if (!mimeType) return false;
  return mimeType.startsWith('video/') || mimeType === GALLERY_MIME;
}
