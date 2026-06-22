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
  return (await res.json()).data as GenerationsPage;
}

/**
 * Returns the URL for streaming/downloading the raw file of a generated-media item.
 * Callers must pass auth headers when fetching this URL directly.
 */
export function generatedMediaFileUrl(id: string): string {
  return `${getApiUrl()}/api/v1/generated-media/${id}/file`;
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
