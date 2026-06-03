import type { Video } from '../types';

/**
 * Per-asset menu decisions for the mobile audio detail ⋮ menu.
 *
 * Mirrors the PC `DownloadMenuDropdown` model: each asset (audio, cover) is
 * judged on its OWN `*_download_status` + whether the file is actually on disk
 * — "download what's present, fetch what's missing" — instead of a single
 * all-or-nothing Download. This is what lets a qishui track whose audio is
 * downloaded but whose cover failed/never-fetched still offer "Fetch Cover".
 *
 * Cover/audio re-fetch on qishui both go through the soda re-download path
 * (the douyin/yt-dlp generic fetch can't service a soda track); the soda
 * workflow tops up a missing cover even when the audio already exists (#474).
 * Non-qishui covers/audio aren't fetchable from this screen — it only renders
 * for soda audio tracks — so we don't surface a (broken) fetch for them.
 */
export type AudioAssetAction = {
  asset: 'audio' | 'cover';
  kind: 'download' | 'fetch' | 'retry';
};

const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
const isFailed = (s?: string) => s?.toLowerCase() === 'failed';

export function audioMenuActions(
  video: Video,
  opts: { hasAudio: boolean; isQishui: boolean },
): AudioAssetAction[] {
  const items: AudioAssetAction[] = [];

  // Audio: file present → download; missing → fetch/retry (qishui only).
  if (opts.hasAudio) {
    items.push({ asset: 'audio', kind: 'download' });
  } else if (opts.isQishui) {
    items.push({ asset: 'audio', kind: isFailed(video.music_download_status) ? 'retry' : 'fetch' });
  }

  // Cover: completed AND file on disk → download; otherwise fetch/retry
  // (qishui only — a "completed" status with no file is still fetchable).
  const coverDone = isCompleted(video.cover_download_status) && !!video.cover_download_path;
  if (coverDone) {
    items.push({ asset: 'cover', kind: 'download' });
  } else if (opts.isQishui) {
    items.push({ asset: 'cover', kind: isFailed(video.cover_download_status) ? 'retry' : 'fetch' });
  }

  return items;
}
