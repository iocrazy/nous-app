// frontend/utils/mediaUrl.ts
import { getApiUrl } from './apiConfig';

/**
 * Check if a URL is already absolute (http/https).
 */
const isAbsoluteUrl = (url: string): boolean =>
  url.startsWith('http://') || url.startsWith('https://');

/**
 * Append a signed media token as a query parameter.
 */
const appendToken = (url: string, token?: string): string => {
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
};

/**
 * Convert a relative or absolute file path to a backend /media/ URL.
 *
 * Supports:
 * - Already-absolute URLs (returned as-is)
 * - Relative paths: "2026-01/video.mp4" → "{apiUrl}/media/2026-01/video.mp4"
 * - Absolute paths: "/data/media/2026-01/video.mp4" → "{apiUrl}/media/2026-01/video.mp4"
 */
export function buildMediaUrl(path: string, token?: string): string {
  if (isAbsoluteUrl(path)) return path;

  const base = getApiUrl();
  let url: string;

  if (!path.startsWith('/')) {
    url = `${base}/media/${path}`;
  } else {
    const match = path.match(/(\d{4}-\d{2}\/.+)$/);
    url = match ? `${base}/media/${match[1]}` : `${base}/media${path}`;
  }

  return appendToken(url, token);
}

/**
 * Get HLS stream URL.
 */
export function buildStreamUrl(hlsPath: string): string {
  return `${getApiUrl()}/stream/${hlsPath}`;
}

/**
 * Get video playback URL.
 * Priority: HLS > download_path > undefined
 */
export function getPlaybackUrl(
  data: {
    media_format?: string;
    hls_path?: string;
    download_path?: string;
  },
  token?: string,
): string | undefined {
  if (data.media_format === 'hls' && data.hls_path) {
    return buildStreamUrl(data.hls_path);
  }
  if (data.download_path && data.download_path !== '#') {
    return buildMediaUrl(data.download_path, token);
  }
  return undefined;
}

/**
 * Get cover image URL.
 * Priority: cover_download_path > cover_urls[0] > dynamic_cover_url > image_download_urls[0]
 */
export function getCoverImageUrl(
  data: {
    cover_download_path?: string;
    cover_urls?: string[];
    dynamic_cover_url?: string;
    image_download_urls?: string[];
  },
  token?: string,
): string | undefined {
  if (data.cover_download_path && data.cover_download_path !== '#') {
    return buildMediaUrl(data.cover_download_path, token);
  }
  if (data.cover_urls?.[0] && data.cover_urls[0] !== '#') {
    return data.cover_urls[0];
  }
  if (data.dynamic_cover_url && data.dynamic_cover_url !== '#') {
    return data.dynamic_cover_url;
  }
  return data.image_download_urls?.[0];
}

/**
 * Check if a URL is a direct playable media URL (not a webpage).
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
    return true;
  }
};
