// frontend/utils/zipExport.ts
//
// Client-side bulk export: package selected media into a single ZIP downloaded
// to the user's machine. Approach A — zip in the browser (uses client memory,
// keeps load off the server). Each item resolves to its primary media file by
// media_type (audio -> /music, gallery -> /gallery zip, else video -> mp4).

import JSZip from 'jszip';

import { getDownloadUrl, getGalleryZipUrl, getMusicDownloadUrl } from '../services/dataService';
import type { Video } from '../types';
import { getAuthHeaders } from './download';

export type ExportKind = 'audio' | 'gallery' | 'video';

const ILLEGAL = /[\\/:*?"<>|]/g;

function sanitize(s: string): string {
  // Strip filesystem-illegal chars; keep spaces/hyphens.
  const cleaned = (s || '').replace(ILLEGAL, '_').trim().slice(0, 120);
  return cleaned || 'media';
}

/** Map a parsed_media.media_type wire value to the primary downloadable kind. */
export function downloadKind(mediaType?: string | null): ExportKind {
  const mt = String(mediaType ?? '').toLowerCase();
  if (mt === 'audio') return 'audio';
  if (['carousel', 'image_text', 'album', 'gallery', '2', '68'].includes(mt)) return 'gallery';
  return 'video';
}

/** Resolve an item to its download URL + filename inside the zip. */
export function resolveExport(v: Video): { url: string; filename: string } {
  // Filename = "Author - Title" (falls back to just Title when no author).
  const titlePart = sanitize(v.title || v.platform_id || 'media');
  const author = (v.author || '').trim();
  const base = author ? `${sanitize(author)} - ${titlePart}` : titlePart;
  switch (downloadKind(v.media_type)) {
    case 'audio':
      return { url: getMusicDownloadUrl(v.platform_id), filename: `${base}.mp3` };
    case 'gallery':
      return { url: getGalleryZipUrl(v.platform_id), filename: `${base}.zip` };
    default:
      return { url: getDownloadUrl(v.platform_id), filename: `${base}.mp4` };
  }
}

export interface ZipProgress {
  done: number;
  total: number;
  failed: number;
}

/**
 * Fetch each item's media file, zip them in-browser, and trigger a single
 * .zip download. Files that fail to fetch are skipped (counted in `failed`).
 * Returns once the download has been triggered (or all items failed).
 */
export async function exportMediaAsZip(
  items: Video[],
  opts?: { zipName?: string; onProgress?: (p: ZipProgress) => void },
): Promise<{ ok: number; failed: number }> {
  const zip = new JSZip();
  const headers = await getAuthHeaders();
  const seen = new Set<string>();
  let ok = 0;
  let failed = 0;

  for (let i = 0; i < items.length; i++) {
    const v = items[i];
    if (!v.platform_id) {
      failed += 1;
      opts?.onProgress?.({ done: i + 1, total: items.length, failed });
      continue;
    }
    const { url, filename } = resolveExport(v);
    // Dedup identical filenames within the archive (same title -> suffix id).
    let name = filename;
    if (seen.has(name)) {
      const dot = name.lastIndexOf('.');
      const stem = dot > 0 ? name.slice(0, dot) : name;
      const ext = dot > 0 ? name.slice(dot) : '';
      name = `${stem}_${v.platform_id}${ext}`;
    }
    seen.add(name);

    try {
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      zip.file(name, await res.blob());
      ok += 1;
    } catch (err) {
      console.error('[zipExport] failed for', v.platform_id, err);
      failed += 1;
    }
    opts?.onProgress?.({ done: i + 1, total: items.length, failed });
  }

  if (ok === 0) return { ok: 0, failed };

  const content = await zip.generateAsync({ type: 'blob' });
  const blobUrl = URL.createObjectURL(content);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = opts?.zipName || `mediahub-export-${ok}-items.zip`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);

  return { ok, failed };
}
