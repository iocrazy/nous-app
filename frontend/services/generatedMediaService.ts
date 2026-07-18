// frontend/services/generatedMediaService.ts
// Service layer for the /api/v1/generated-media endpoint.

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface GenerationItem {
  id: string;
  media_kind: string;
  mime?: string;
  prompt?: string;
  model?: string;
  provider?: string;
  origin_kind: string;
  canvas_id?: string;
  created_at: string;
}

export interface GenerationsPage {
  items: GenerationItem[];
  next_cursor: string | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Coerce an untrusted `data` payload into a well-formed GenerationsPage.
 * The backend contract is `{ items: [...], next_cursor: ... }`, but a bare
 * `[]`, `null`, or a page missing `items` must never leak `undefined` to
 * callers that read `.items` (regression #1457: the entity asset strip crashed
 * into its error boundary on exactly this shape).
 */
function normalizeGenerationsPage(raw: unknown): GenerationsPage {
  const page = (raw && typeof raw === 'object' ? raw : {}) as Partial<GenerationsPage>;
  return {
    items: Array.isArray(page.items) ? page.items : [],
    next_cursor: page.next_cursor ?? null,
  };
}

// ─── API calls ────────────────────────────────────────────────────────────────

/**
 * Fetch a page of generated-media items.
 * Accepts optional cursor (for keyset pagination) and kind filter.
 */
export async function fetchGenerations(
  cursor?: string,
  kind?: string,
): Promise<GenerationsPage> {
  const qs = new URLSearchParams();
  if (cursor) qs.set('cursor', cursor);
  if (kind) qs.set('kind', kind);

  const res = await fetch(`${getApiUrl()}/api/v1/generated-media?${qs}`, {
    headers: await getAuthHeaders(),
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return normalizeGenerationsPage((await res.json()).data);
}

/**
 * Fetch the generations backlinked to one library entity (CC5 asset strip).
 * Newest first; one page is plenty for a card strip.
 */
export async function fetchEntityGenerations(
  entityKind: 'character' | 'location' | 'prop',
  entityId: string,
  limit = 24,
): Promise<GenerationsPage> {
  const qs = new URLSearchParams({
    entity_kind: entityKind,
    entity_id: entityId,
    limit: String(limit),
  });
  const res = await fetch(`${getApiUrl()}/api/v1/generated-media?${qs}`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return normalizeGenerationsPage((await res.json()).data);
}

/**
 * Returns the URL for streaming/downloading the raw file of a generated-media item.
 * Callers must pass auth headers when fetching this URL directly.
 */
export function generatedMediaFileUrl(id: string): string {
  return `${getApiUrl()}/api/v1/generated-media/${id}/file`;
}

/**
 * Returns the no-auth video stream URL (Range-capable) for a generated-media
 * item. Safe for a bare <video src> — no Authorization header needed.
 */
export function generatedMediaStreamUrl(id: string): string {
  return `${getApiUrl()}/api/v1/generated-media/${id}/stream`;
}

/**
 * Returns the no-auth cover/thumbnail URL for a generated-media item.
 * Safe to use directly in browser <img src> without Authorization headers.
 */
export function generatedMediaCoverUrl(id: string): string {
  return `${getApiUrl()}/api/v1/generated-media/${id}/cover`;
}

/**
 * Promotes a generated-media item to the library (creates a Resource row).
 * Idempotent — re-calling returns the existing resource.
 */
export async function promoteGeneration(genId: string): Promise<{ promoted_resource_id: string }> {
  const res = await fetch(`${getApiUrl()}/api/v1/generated-media/${genId}/promote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()).data;
}
