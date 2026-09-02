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

/**
 * Sub-classification of a canvas upload, sent as the `role` form field and
 * stored by the backend in `origin.params.role`
 * (`app/services/library/generated_roles.py` owns the vocabulary — keep the
 * two lists in step).
 *
 * `POST /generated-media/import` is not one thing: a person dropping a file
 * on the canvas and the mask/brush editors baking a PNG both go through it,
 * and until this existed the Generated inbox showed them side by side as if a
 * writer were meant to triage a black-and-white mask. Absent means
 * `user_upload` — the visible default — so every existing caller keeps its
 * behaviour by saying nothing.
 */
export type CanvasUploadRole = 'user_upload' | 'mask' | 'brush';

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
  role?: CanvasUploadRole,
): Promise<GeneratedImageRef> {
  const form = new FormData();
  form.append('file', file);
  if (canvasId) form.append('canvas_id', canvasId);
  if (nodeId) form.append('node_id', nodeId);
  // Omitted rather than sent as 'user_upload': the backend's absent-means-
  // visible default is the single place that rule lives, and a client that
  // spelled it out would be a second copy of it.
  if (role && role !== 'user_upload') form.append('role', role);
  const response = await apiFetch('/api/v1/generated-media/import', {
    method: 'POST',
    raw: form,
  });
  const json = (await response.json()) as {
    data?: { id?: string; url?: string; media_kind?: string };
  };
  const data = json?.data;
  if (!data?.url) {
    throw new Error('import returned no url');
  }
  return {
    url: data.url,
    kind: data.media_kind === 'video' ? 'video' : 'image',
    name: file.name,
    // The backend has always sent this; nothing read it until Cover Studio
    // needed the row id to save a template. String, not number — snowflake.
    id: data.id,
  };
}

/**
 * Library-asset ingest: mint a durable /generated-media/ URL for an
 * already-uploaded resource-library asset via
 * POST /generated-media/import-from-resource. Resource-library file URLs
 * are auth-gated and the i2i bridge (promptInputs.ts's DURABLE_PREFIX)
 * can't fetch them directly, so a Library pick needs this same durable
 * detour importCanvasMedia does for local uploads.
 */
export async function importResourceAsCanvasMedia(
  resourceId: string,
): Promise<{ url: string; kind: 'image' | 'video'; id?: string }> {
  const response = await apiFetch('/api/v1/generated-media/import-from-resource', {
    method: 'POST',
    json: { resource_id: resourceId },
  });
  const json = (await response.json()) as {
    data?: { id?: string; url?: string; media_kind?: string };
  };
  const data = json?.data;
  if (!data?.url) {
    throw new Error('import-from-resource returned no url');
  }
  return {
    url: data.url,
    kind: data.media_kind === 'video' ? 'video' : 'image',
    id: data.id,
  };
}
