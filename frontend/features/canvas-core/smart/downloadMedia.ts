// features/canvas-core/smart/downloadMedia.ts
//
// Shared blob-download helper (extracted from OutputLightbox for the node
// toolbar, P2-3). fetch→blob→anchor: the `download` attribute is ignored
// on cross-origin URLs (the API host differs from the app origin), so a
// plain anchor would navigate away instead of saving.

import { fullResSrc } from './mediaUrl';

export interface DownloadableItem {
  url: string;
  name?: string;
}

export function downloadName(item: DownloadableItem, fallbackIndex: number): string {
  if (item.name) return item.name;
  const tail = item.url.split('/').filter(Boolean).pop() ?? '';
  return tail || `output-${fallbackIndex + 1}`;
}

/** Save an already-in-memory blob (P2-8 video frame export) — same
 *  object-URL anchor dance, no fetch. */
export function downloadBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = href;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(href);
  }
}

export async function downloadUrl(
  item: DownloadableItem,
  fallbackIndex: number,
): Promise<void> {
  // Full resolution: this hands the user a file. Saving the 1024px preview
  // under the original's filename would be a silent quality downgrade.
  const res = await fetch(fullResSrc(item.url));
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = href;
    link.download = downloadName(item, fallbackIndex);
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(href);
  }
}
