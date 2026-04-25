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
  if (isAbsoluteUrl(id)) return id;
  return appendToken(`${getApiUrl()}/media/${id}/cover`, token);
}

/**
 * Get HLS stream URL.
 */
export function buildStreamUrl(hlsPath: string): string {
  return `${getApiUrl()}/stream/${hlsPath}`;
}

/**
 * Status helper — only "completed" means the file is actually on disk.
 *
 * Some rows in `parsed_media` have a path field set even though the latest
 * download attempt failed (we never clear path on failure — see
 * downloader.py:1298). Trusting `path` alone yields 404s. The status field
 * is the ground truth.
 */
const isStatusCompleted = (status?: string | null): boolean =>
  typeof status === 'string' && status.toLowerCase() === 'completed';

/**
 * Get video playback URL by media/resource ID.
 * Priority: HLS > ID-based /media/ URL > undefined
 *
 * Local route only when video_download_status === 'completed' AND path set.
 */
export function getPlaybackUrl(
  data: {
    id?: string;
    media_format?: string;
    hls_path?: string;
    download_path?: string;
    video_download_status?: string;
  },
  token?: string,
): string | undefined {
  if (data.media_format === 'hls' && data.hls_path) {
    return buildStreamUrl(data.hls_path);
  }
  if (
    data.id
    && data.download_path
    && data.download_path !== '#'
    && isStatusCompleted(data.video_download_status)
  ) {
    return buildMediaUrl(String(data.id), token);
  }
  return undefined;
}

/**
 * Get cover image URL — local-only, status-gated.
 *
 * We never hand a remote CDN URL (bilibili / douyin / xhs) to the browser:
 * those CDNs hot-link-protect on Referer and respond 403 to direct GETs,
 * which spams the console AND risks getting our IP banned.
 *
 * Returns the local proxy URL only when BOTH:
 *   - cover_download_path is set
 *   - cover_download_status === 'completed'
 *
 * Otherwise returns undefined and the caller renders a placeholder. This
 * defends against orphan rows where path was set on a previous successful
 * download but the file has since been removed (status reverted to
 * failed / null).
 */
export function getCoverImageUrl(
  data: {
    id?: string;
    cover_download_path?: string;
    cover_download_status?: string;
  },
  token?: string,
): string | undefined {
  if (
    data.id
    && data.cover_download_path
    && data.cover_download_path !== '#'
    && isStatusCompleted(data.cover_download_status)
  ) {
    return buildMediaCoverUrl(String(data.id), token);
  }
  return undefined;
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
