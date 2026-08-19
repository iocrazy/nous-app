// features/canvas-core/smart/mediaEditBridge.ts
// B3: media-card items are generated_media rows; every derive-* editing
// endpoint wants a resources id. genIdFromDurableUrl parses the row id out
// of the durable url shape (/api/v1/generated-media/{id}/file|cover|stream),
// ensureResourceId promotes it exactly once per url (module cache) via the
// backend's promote endpoint.

import { promoteGeneration } from '../services/canvasGenerationService';

const DURABLE_RE = /\/api\/v1\/generated-media\/(\d+)\/(?:file|cover|stream)\b/;

export function genIdFromDurableUrl(url: string): string | null {
  const m = DURABLE_RE.exec(url);
  return m ? m[1] : null;
}

const promoted = new Map<string, Promise<string>>();

/** null for non-durable urls (nothing to promote). */
export function ensureResourceId(url: string): Promise<string | null> {
  const genId = genIdFromDurableUrl(url);
  if (!genId) return Promise.resolve(null);
  let pending = promoted.get(url);
  if (!pending) {
    pending = promoteGeneration(genId);
    // A failed promote must not be cached — retry on next attempt.
    pending.catch(() => promoted.delete(url));
    promoted.set(url, pending);
  }
  return pending;
}

export function _resetPromoteCache(): void {
  promoted.clear();
}
