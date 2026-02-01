/**
 * aweme_type 类型映射
 * - 0: 标准视频
 * - 2: 图片轮播/图集
 * - 4: 特殊视频类型
 * - 61: 另一种特殊视频变体
 * - 68: 图文类型
 */

export const AWEME_TYPE_MAP: Record<string | number, string> = {
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

/**
 * 判断是否是视频类型
 */
export const isVideoType = (awemeType?: string | number): boolean => {
  if (awemeType === undefined || awemeType === null) return false;
  const type = String(awemeType);
  return type === '0' || type === '4' || type === '61';
};

/**
 * 判断是否是图集类型
 */
export const isAlbumType = (awemeType?: string | number): boolean => {
  if (awemeType === undefined || awemeType === null) return false;
  const type = String(awemeType);
  return type === '2' || type === '68';
};

/**
 * 获取类型显示文本
 */
export const getAwemeTypeLabel = (awemeType?: string | number): string => {
  if (awemeType === undefined || awemeType === null) return 'Unknown';
  return AWEME_TYPE_MAP[awemeType] || AWEME_TYPE_MAP[String(awemeType)] || 'Unknown';
};

// API 基础地址 - 空字符串表示使用相对路径（通过 Vite 代理）
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

/**
 * 将文件路径转换为后端静态文件 URL
 *
 * 支持两种格式：
 * 1. 相对路径（新格式）: 2026-01/xxx.mp4 -> http://localhost:8080/media/2026-01/xxx.mp4
 * 2. 绝对路径（旧格式兼容）: /path/to/base/2026-01/xxx.mp4 -> http://localhost:8080/media/2026-01/xxx.mp4
 */
const convertPathToUrl = (path: string): string => {
  // 如果已经是 URL，直接返回
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }

  // 如果是相对路径（新格式：不以 / 开头），直接拼接 /media/
  if (!path.startsWith('/')) {
    return `${getApiUrl()}/media/${path}`;
  }

  // 绝对路径（旧格式兼容）：提取年月和文件名部分
  // 例如: /Volumes/xxx/2026-01/file.mp4 -> /media/2026-01/file.mp4
  const yearMonthMatch = path.match(/(\d{4}-\d{2}\/[^/]+)$/);
  if (yearMonthMatch) {
    return `${getApiUrl()}/media/${yearMonthMatch[1]}`;
  }

  // 无法识别的格式，返回完整 media 路径
  return `${getApiUrl()}/media${path}`;
};

/**
 * 获取视频播放地址
 * 优先级: download_path > video_download_urls[0] > video_original_url
 */
export const getVideoUrl = (data: {
  download_path?: string;
  video_download_urls?: string[];
  video_original_url?: string;
}): string | undefined => {
  // 优先使用 download_path (转换为后端静态文件 URL)
  if (data.download_path && data.download_path !== '#') {
    return convertPathToUrl(data.download_path);
  }
  // 其次使用 video_download_urls
  if (data.video_download_urls?.[0] && data.video_download_urls[0] !== '#') {
    return data.video_download_urls[0];
  }
  // 最后使用原始链接
  return data.video_original_url;
};

/**
 * 获取封面图片地址
 * 优先级: cover_download_path > cover_urls[0] > dynamic_cover_url > image_download_urls[0]
 */
export const getCoverUrl = (data: {
  cover_download_path?: string;
  cover_urls?: string[];
  dynamic_cover_url?: string;
  image_download_urls?: string[];
}): string | undefined => {
  // 优先使用 cover_download_path (转换为后端静态文件 URL)
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
