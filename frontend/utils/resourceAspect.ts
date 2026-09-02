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
import { isAlbumType, isAudioType, isVideoType } from './awemeType'

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
  /** `resources` rows carry a MIME type. */
  mime_type?: string | null
  /**
   * `parsed_media` rows (My Downloads) carry a media_type instead — 'video' /
   * 'carousel' / 'image_text' / 'audio', plus legacy numeric spellings. Both
   * shapes feed the same helpers so the two surfaces cannot drift apart.
   */
  media_type?: string | number | null
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

function isImageLike(source?: AspectSource): boolean {
  if (source?.mime_type?.startsWith('image/')) return true
  // Albums and image-text posts are still pictures, so they measure like one.
  return isAlbumType(source?.media_type ?? undefined)
}

function isVideoLike(source?: AspectSource): boolean {
  if (source?.mime_type?.startsWith('video/')) return true
  const mt = source?.media_type
  if (mt === undefined || mt === null) return false
  return isVideoType(mt) && !isAudioType(String(mt))
}

/**
 * True when the thumbnail is worth measuring: a visual file whose dimensions
 * the server did not give us. Callers use this to attach the load handler only
 * where it can change the layout.
 */
export function needsAspectMeasurement(resource?: AspectSource): boolean {
  if (!resource) return false
  if (parseResolution(resource.resolution) !== null) return false
  return isImageLike(resource) || isVideoLike(resource)
}

/**
 * Relative tolerance under which a measured ratio is treated as confirming the
 * placeholder rather than correcting it.
 *
 * Reporting a ratio that rounds to what the layout already assumed would
 * repartition every row after that item (the packing is sequential — see
 * utils/justifiedLayout.ts) to produce a visually identical result. A 4:3 photo
 * landing on the 4:3 placeholder is the common case worth skipping.
 */
export const ASPECT_MATCH_TOLERANCE = 0.02

/**
 * True when `measured` is close enough to the ratio the layout is already using
 * that re-laying out would not visibly change anything.
 */
export function matchesCurrentAspect(
  current: number,
  measured: number,
  tolerance: number = ASPECT_MATCH_TOLERANCE,
): boolean {
  if (!Number.isFinite(measured) || measured <= 0 || current <= 0) return false
  return Math.abs(clampAspectRatio(measured) - current) / current <= tolerance
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

  if (isVideoLike(resource)) return DEFAULT_VIDEO_ASPECT
  if (isImageLike(resource)) return DEFAULT_IMAGE_ASPECT
  return NON_VISUAL_ASPECT
}
