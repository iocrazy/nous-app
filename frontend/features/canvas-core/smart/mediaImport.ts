// features/canvas-core/smart/mediaImport.ts
//
// Upload-node ingest: push a local file into Tier-1 generated-media via
// POST /generated-media/import so the media node holds DURABLE URLs —
// the same /generated-media/ form the generation bridge accepts as i2i
// sources (promptInputs.durableImagesOf). Resource-library uploads are a
// different path on purpose: their file URLs are auth-gated and the
// bridge can't fetch them.

import { apiFetch } from '../../../services/apiClient';
import type { GeneratedImageRef } from './types';

export const CANVAS_MEDIA_ACCEPT = 'image/*,video/*';
export const CANVAS_MEDIA_MAX_BYTES = 50 * 1024 * 1024; // backend cap

export function isImportableCanvasFile(file: File): boolean {
  const mime = (file.type || '').toLowerCase();
  return mime.startsWith('image/') || mime.startsWith('video/');
}

export async function importCanvasMedia(
  file: File,
  canvasId: string | null,
  nodeId: string | null,
): Promise<GeneratedImageRef> {
  const form = new FormData();
  form.append('file', file);
  if (canvasId) form.append('canvas_id', canvasId);
  if (nodeId) form.append('node_id', nodeId);
  const response = await apiFetch('/api/v1/generated-media/import', {
    method: 'POST',
    raw: form,
  });
  const json = (await response.json()) as {
    data?: { url?: string; media_kind?: string };
  };
  const data = json?.data;
  if (!data?.url) {
    throw new Error('import returned no url');
  }
  return {
    url: data.url,
    kind: data.media_kind === 'video' ? 'video' : 'image',
    name: file.name,
  };
}
