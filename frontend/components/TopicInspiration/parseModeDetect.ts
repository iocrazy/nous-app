// frontend/components/TopicInspiration/parseModeDetect.ts
export type ParseMode = 'single' | 'batch' | 'playlist';

export interface ParseDetection {
  mode: ParseMode;
  count: number;
  label: string;
}

const URL_RE = /https?:\/\/[^\s]+/g;
// 'qishui' covers Soda Music (汽水音乐) share links (qishui.douyin.com /
// music.douyin.com/qishui) — both tracks and playlists go through the Soda
// playlist resolver. Plain Douyin video links (v.douyin.com) stay single.
const PLAYLIST_HINTS = ['/playlist', 'music.', '/songlist', 'list=', 'qishui'];

export function detectParseMode(input: string): ParseDetection {
  const text = (input || '').trim();
  const urls = text.match(URL_RE) || [];
  if (urls.length === 0) {
    return { mode: 'single', count: 0, label: 'Paste a link' };
  }
  if (urls.length > 1) {
    return { mode: 'batch', count: urls.length, label: `Batch · ${urls.length} links` };
  }
  const single = urls[0].toLowerCase();
  if (PLAYLIST_HINTS.some((h) => single.includes(h))) {
    return { mode: 'playlist', count: 1, label: 'Playlist detected' };
  }
  return { mode: 'single', count: 1, label: 'Single link' };
}
