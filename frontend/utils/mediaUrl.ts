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
 * Build a media URL by resource/media ID.
 * Backend resolves ID → file path internally; the URL never exposes filenames.
 *
 * URL pattern: /media/{id}?token=signed_token
 */
export function buildMediaUrl(id: string, token?: string): string {
  if (isAbsoluteUrl(id)) return id;
  return appendToken(`${getApiUrl()}/media/${id}`, token);
}

/**
 * Build a cover image URL by resource/media ID.
 * URL pattern: /media/{id}/cover?token=signed_token
 */
export function buildMediaCoverUrl(id: string, token?: string): string {
  return appendToken(`${getApiUrl()}/media/${id}/cover`, token);
}

/**
 * Get HLS stream URL.
 */
export function buildStreamUrl(hlsPath: string): string {
  return `${getApiUrl()}/stream/${hlsPath}`;
}

/**
 * Get video playback URL by media/resource ID.
 * Priority: HLS > ID-based /media/ URL > undefined
 */
export function getPlaybackUrl(
  data: {
    id?: string;
    media_format?: string;
    hls_path?: string;
    download_path?: string;
  },
  token?: string,
): string | undefined {
  if (data.media_format === 'hls' && data.hls_path) {
    return buildStreamUrl(data.hls_path);
  }
  // Use ID-based URL (preferred) — backend resolves file path from DB
  if (data.id && data.download_path && data.download_path !== '#') {
    return buildMediaUrl(String(data.id), token);
  }
  return undefined;
}

/**
 * Get cover image URL.
 * Priority: ID-based cover URL > cover_urls[0] > dynamic_cover_url > image_download_urls[0]
 */
export function getCoverImageUrl(
  data: {
    id?: string;
    cover_download_path?: string;
    cover_urls?: string[];
    dynamic_cover_url?: string;
    image_download_urls?: string[];
  },
  token?: string,
): string | undefined {
  // Local cover via ID-based route
  if (data.id && data.cover_download_path && data.cover_download_path !== '#') {
    return buildMediaCoverUrl(String(data.id), token);
  }
  // External CDN URLs (no auth needed)
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
