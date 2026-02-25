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

// API base URL
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

/**
 * Convert file path to backend static file URL
 *
 * Supports two formats:
 * 1. Relative path (new format): 2026-01/xxx.mp4 -> http://localhost:8080/media/2026-01/xxx.mp4
 * 2. Absolute path (legacy compat): /path/to/base/2026-01/xxx.mp4 -> http://localhost:8080/media/2026-01/xxx.mp4
 */
const convertPathToUrl = (path: string): string => {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }

  if (!path.startsWith('/')) {
    return `${getApiUrl()}/media/${path}`;
  }

  const yearMonthMatch = path.match(/(\d{4}-\d{2}\/[^/]+)$/);
  if (yearMonthMatch) {
    return `${getApiUrl()}/media/${yearMonthMatch[1]}`;
  }

  return `${getApiUrl()}/media${path}`;
};

/**
 * Get stream URL for HLS content
 */
export const getStreamUrl = (hlsPath: string): string => {
  return `${getApiUrl()}/stream/${hlsPath}`;
};

/**
 * Check if a URL is a direct playable media URL (not a webpage).
 * Webpage URLs from platforms like bilibili/youtube can't be used as <video src>.
 */
export const isPlayableUrl = (url: string): boolean => {
  try {
    const host = new URL(url).hostname;
    const pageHosts = [
      'www.bilibili.com', 'bilibili.com',
      'www.youtube.com', 'youtube.com', 'youtu.be',
      'www.douyin.com', 'douyin.com',
      'www.tiktok.com', 'tiktok.com',
      'www.xiaohongshu.com', 'xiaohongshu.com',
      'twitter.com', 'x.com',
    ];
    return !pageHosts.includes(host);
  } catch {
    // Not a valid URL (likely a relative path) — treat as playable
    return true;
  }
};

/**
 * Get video playback URL
 * Priority: HLS > download_path > video_download_urls[0] (if playable) > undefined
 * Note: original_url is NOT used as video src — it's typically a webpage URL.
 */
export const getVideoUrl = (data: {
  media_format?: string;
  hls_path?: string;
  download_path?: string;
}): string | undefined => {
  // HLS format: return stream URL
  if (data.media_format === 'hls' && data.hls_path) {
    return getStreamUrl(data.hls_path);
  }
  // download_path (convert to backend static file URL)
  if (data.download_path && data.download_path !== '#') {
    return convertPathToUrl(data.download_path);
  }
  // Only play verified local files (HLS or download_path).
  // CDN URLs (video_download_urls) are not used — can't confirm download succeeded.
  return undefined;
};

/**
 * Get cover image URL
 * Priority: cover_download_path > cover_urls[0] > dynamic_cover_url > image_download_urls[0]
 */
export const getCoverUrl = (data: {
  cover_download_path?: string;
  cover_urls?: string[];
  dynamic_cover_url?: string;
  image_download_urls?: string[];
}): string | undefined => {
  if (data.cover_download_path && data.cover_download_path !== '#') {
    return convertPathToUrl(data.cover_download_path);
  }
  if (data.cover_urls?.[0] && data.cover_urls[0] !== '#') {
    return data.cover_urls[0];
  }
  if (data.dynamic_cover_url && data.dynamic_cover_url !== '#') {
    return data.dynamic_cover_url;
  }
  return data.image_download_urls?.[0];
};
