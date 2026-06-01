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
 * Check if media type is a video type
 */
export const isVideoType = (mediaType?: string | number): boolean => {
  if (mediaType === undefined || mediaType === null) return false;
  const type = String(mediaType);
  return type === 'video' || type === 'special' || type === 'short' || type === 'live_clip' ||
         type === '0' || type === '4' || type === '61';
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
