/**
 * Display aspect ratio (w/h) for a resource, used by the adaptive
 * ("justified") view to size thumbnails by their real proportions.
 *
 * Where the number comes from, in priority order:
 *
 *  1. `resource.resolution` ("1920x1080") — authoritative, no measurement
 *     needed. Only the download path populates it (ytdlp_service writes
 *     `{width}x{height}`); the upload router never captures image dimensions,
 *     so every file in My Uploads has resolution = NULL.
 *  2. A ratio measured from the loaded thumbnail's natural dimensions. This is
 *     what covers My Uploads, and it covers already-uploaded files too — a
 *     backend change would only help future uploads and would still need a
 *     backfill.
 *  3. A per-kind placeholder, used for the single layout pass before the
 *     thumbnail loads.
 *
 * Non-visual files (documents, archives, audio) get a square tile and are
 * never measured: their thumbnail is an icon, whose proportions say nothing
 * about the file.
 */

import { clampAspectRatio } from './justifiedLayout'

/** Placeholder for an image whose dimensions are not known yet. */
export const DEFAULT_IMAGE_ASPECT = 4 / 3
/** Placeholder for a video whose dimensions are not known yet. */
export const DEFAULT_VIDEO_ASPECT = 16 / 9
/** Everything non-visual renders as a square tile in the same row flow. */
export const NON_VISUAL_ASPECT = 1

// The clamp lives with the layout it protects (one pathological panorama must
// not eat a whole row); re-exported here so callers have a single import.
export { clampAspectRatio } from './justifiedLayout'

export interface AspectSource {
  resolution?: string | null
  mime_type?: string | null
  thumbnail_path?: string | null
}

/** Parse "1920x1080" / "1920:1080" / "1920 × 1080" into a ratio. */
export function parseResolution(res?: string | null): number | null {
  if (!res) return null
  const m = res.match(/(\d+)\s*[x:×]\s*(\d+)/i)
  if (!m) return null
  const w = Number(m[1])
  const h = Number(m[2])
  if (!(w > 0) || !(h > 0)) return null
  return clampAspectRatio(w / h)
}

function isImage(mime?: string | null): boolean {
  return !!mime && mime.startsWith('image/')
}

function isVideo(mime?: string | null): boolean {
  return !!mime && mime.startsWith('video/')
}

/**
 * True when the thumbnail is worth measuring: a visual file whose dimensions
 * the server did not give us. Callers use this to attach the load handler only
 * where it can change the layout.
 */
export function needsAspectMeasurement(resource?: AspectSource): boolean {
  if (!resource) return false
  if (parseResolution(resource.resolution) !== null) return false
  return isImage(resource.mime_type) || isVideo(resource.mime_type)
}

/**
 * @param measured ratio observed from the loaded thumbnail, if any.
 */
export function aspectRatioOf(resource?: AspectSource, measured?: number): number {
  const fromResolution = parseResolution(resource?.resolution)
  if (fromResolution !== null) return fromResolution

  if (measured !== undefined && Number.isFinite(measured) && measured > 0) {
    return clampAspectRatio(measured)
  }

  if (isVideo(resource?.mime_type)) return DEFAULT_VIDEO_ASPECT
  if (isImage(resource?.mime_type)) return DEFAULT_IMAGE_ASPECT
  return NON_VISUAL_ASPECT
}
