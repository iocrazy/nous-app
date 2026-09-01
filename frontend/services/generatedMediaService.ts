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
  /**
   * Set once the item has been promoted into the library (the `Keep` action).
   * The backend has always returned this column — it is part of the repository
   * projection — but until 2026-08 no frontend read it, so the UI could not
   * tell a kept generation from an unkept one.
   */
  promoted_resource_id?: string | null;
}

export interface GenerationsPage {
  items: GenerationItem[];
  next_cursor: string | null;
}

// ─── Typed failures ───────────────────────────────────────────────────────────

/**
 * Why a mutation on a generated-media item failed, in the terms the user needs
 * to hear. `not-found` deliberately covers BOTH "already deleted" and "not in
 * this workspace": the backend answers both with the same shape so a delete
 * cannot be used to probe for rows in someone else's scope.
 */
export type GeneratedMediaFailure =
  | 'not-found'
  | 'forbidden'
  | 'unauthenticated'
  | 'server'
  | 'network';

/**
 * Thrown by the mutating calls (`promoteGeneration`, `deleteGeneration`) so the
 * caller can render a reason instead of a shrug. Throwing — rather than
 * returning a result union — is deliberate: a caller that forgets to branch
 * gets a loud rejection, never a silent no-op.
 */
export class GeneratedMediaError extends Error {
  constructor(
    readonly reason: GeneratedMediaFailure,
    readonly status?: number,
  ) {
    super(
      `generated-media request failed: ${reason}` +
        (status === undefined ? '' : ` (HTTP ${status})`),
    );
    this.name = 'GeneratedMediaError';
  }
}

/** Map an HTTP status onto the failure vocabulary above. */
export function failureFromStatus(status: number): GeneratedMediaFailure {
  if (status === 401) return 'unauthenticated';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not-found';
  return 'server';
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
 *
 * NO IN-APP CALLER since P3 Task 6: its consumer was `EntityAssetStrip`, the
 * strip on the retired `CharacterLibrary` / `EntityLibrary` bible cards. Kept
 * on purpose rather than deleted, because the WRITE half is still live and
 * P4-owned — `smart/entityRef.ts` resolves the card a prompt hangs off and
 * `generationRunner` stamps `entity_kind` / `entity_id` into
 * `generated_media.params` on every canvas dispatch. Deleting the only reader
 * would leave that stamp with nothing that can ever read it back. P4 owns the
 * decision: re-point this at assets (`canvas_asset_refs`) or retire both
 * halves together. Its own test in `generatedMediaService.test.ts` stays.
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
  let res: Response;
  try {
    res = await fetch(`${getApiUrl()}/api/v1/generated-media/${genId}/promote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
    });
  } catch (err) {
    console.error('[generatedMedia] promote request never reached the server:', err);
    throw new GeneratedMediaError('network');
  }
  if (!res.ok) throw new GeneratedMediaError(failureFromStatus(res.status), res.status);
  return (await res.json()).data;
}

/**
 * Permanently deletes a generated-media item: the row, plus its backing object
 * when no other generation still points at the same content-addressed key.
 *
 * Lifecycle note (verified before this was wired up): a generation that has
 * already been `Keep`-ed owns a SEPARATE copy of the bytes — `promote` writes
 * into the `library` bucket (or `teams/<scope>/uploads/...` on the filesystem
 * track), whereas a generation lives in the `chat-media` bucket (or
 * `teams/<scope>/generations/...`). Deleting a generation therefore never
 * disturbs the library asset, and the backend's object cleanup is refcounted
 * across generations only — see
 * `GeneratedMediaRepository._maybe_remove_object`.
 *
 * Rejects with {@link GeneratedMediaError}. A 200 carrying `deleted: false`
 * (row already gone, or outside the caller's scope) becomes `not-found`.
 */
export async function deleteGeneration(genId: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${getApiUrl()}/api/v1/generated-media/${genId}`, {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    });
  } catch (err) {
    console.error('[generatedMedia] delete request never reached the server:', err);
    throw new GeneratedMediaError('network');
  }
  if (!res.ok) throw new GeneratedMediaError(failureFromStatus(res.status), res.status);

  let deleted = false;
  try {
    const body = (await res.json()) as { data?: { deleted?: boolean } } | null;
    deleted = Boolean(body?.data?.deleted);
  } catch (err) {
    console.error('[generatedMedia] delete returned an unreadable body:', err);
    throw new GeneratedMediaError('server', res.status);
  }
  if (!deleted) throw new GeneratedMediaError('not-found', res.status);
}
