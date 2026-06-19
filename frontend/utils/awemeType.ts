/**
 * media_type mapping (generalized from aweme_type)
 * New values: 'video', 'carousel', 'image_text', 'special', 'short', 'live_clip'
 * Legacy numeric values still supported for backward compatibility
 */

export const MEDIA_TYPE_MAP: Record<string | number, string> = {
  // New string-based types
  'video': 'Video',
  'carousel': 'Album',
  'image_text': 'Gallery',
  'special': 'Video',
  'short': 'Short',
  'live_clip': 'Live Clip',
  'audio': 'Audio',
  // Legacy numeric types (backward compatible)
  '0': 'Video',
  '2': 'Album',
  '4': 'Video',
  '61': 'Video',
  '68': 'Gallery',
  0: 'Video',
  2: 'Album',
  4: 'Video',
  61: 'Video',
  68: 'Gallery',
};

// Keep old name as alias
export const AWEME_TYPE_MAP = MEDIA_TYPE_MAP;

/**
 * Check if media type is a video type.
 *
 * Content-driven (NOT a fixed allowlist): anything that isn't an album/image
 * or audio is a downloadable video. Mirrors the backend download gate
 * (`download_strategies.py`: video when `media_type not in (2, 68)`). A fixed
 * (0/4/61) allowlist silently dropped the "Fetch Video" action for newer
 * douyin types like 51 ("翻唱"/cover posts) even though they carry
 * video_download_urls — the user saw only a "Cover" option and could never
 * fetch the video. Inlined album/audio checks keep this self-contained
 * (isVideoType is declared before isAlbumType in this file).
 */
export const isVideoType = (mediaType?: string | number): boolean => {
  if (mediaType === undefined || mediaType === null) return false;
  const type = String(mediaType);
  // Album / image types
  if (type === 'carousel' || type === 'image_text' || type === '2' || type === '68') return false;
  // Audio (handled by its own audio screen / extract flow)
  if (type === 'audio') return false;
  return true;
};

/**
 * Check if media type is an album/gallery type
 */
export const isAlbumType = (mediaType?: string | number): boolean => {
  if (mediaType === undefined || mediaType === null) return false;
  const type = String(mediaType);
  return type === 'carousel' || type === 'image_text' ||
         type === '2' || type === '68';
};

/**
 * Check if media type is an audio type
 */
export const isAudioType = (mediaType?: string): boolean => {
  return mediaType === 'audio';
};

/**
 * Get display label for media type
 */
export const getMediaTypeLabel = (mediaType?: string | number): string => {
  if (mediaType === undefined || mediaType === null) return 'Unknown';
  return MEDIA_TYPE_MAP[mediaType] || MEDIA_TYPE_MAP[String(mediaType)] || 'Unknown';
};

// Keep old name as alias
export const getAwemeTypeLabel = getMediaTypeLabel;

/**
 * Format resolution string: "1080:1920" → "1080x1920"
 */
export const formatResolution = (resolution?: string): string => {
  if (!resolution) return '';
  return resolution.replace(/:/g, 'x');
};

// --- Delegated to mediaUrl.ts ---
import {
  buildStreamUrl,
  getPlaybackUrl,
  getCoverImageUrl,
  isPlayableUrl,
} from './mediaUrl';

// Re-export for backward compatibility
export { buildStreamUrl as getStreamUrl, isPlayableUrl };

/**
 * Get video playback URL (backward-compatible wrapper).
 * Status-gated — only returns a URL when video_download_status === 'completed'.
 */
export const getVideoUrl = (data: {
  id?: string;
  media_format?: string;
  hls_path?: string;
  download_path?: string;
  video_download_status?: string;
}, token?: string): string | undefined => {
  return getPlaybackUrl(data, token);
};

/**
 * Get cover image URL (backward-compatible wrapper).
 * Local-only + status-gated — never returns a remote CDN URL, never returns
 * a stale local path whose backing file may have been cleaned up.
 */
export const getCoverUrl = (data: {
  id?: string;
  cover_download_path?: string;
  cover_download_status?: string;
}, token?: string): string | undefined => {
  return getCoverImageUrl(data, token);
};
