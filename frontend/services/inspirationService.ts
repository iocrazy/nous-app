// API layer for /api/v1/inspiration/* (P1 backend, PR #1137).
// All ids are strings — the backend serializes bigints via coerce_numbers_to_str.
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface NoteAttachment {
  id: string;
  mime: string;
  size_bytes: number;
  original_name: string;
}

export interface RefHotspot {
  hotspot_id?: string;
  title: string;
  source?: string;
  heat?: number;
  url?: string;
  captured_at?: string;
}

export interface InspirationNote {
  id: string;
  content_md: string;
  tags: string[];
  ref_hotspot: RefHotspot | null;
  pinned: boolean;
  /** 0-5 stars (mig 448). Backend defaults it to 0, so this is a number, not
   *  optional — but read it as `rating ?? 0` when the source is a hand-written
   *  fixture that predates the column. */
  rating: number;
  note_date: string;
  created_at: string;
  updated_at: string;
  attachments: NoteAttachment[];
}

export interface NoteFilters {
  date?: string;
  tag?: string;
  q?: string;
  /** 1-5 = only notes rated at least this. 0/undefined = no rating filter. */
  min_rating?: number;
}

export interface ApiToken {
  id: string;
  name: string;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

// Returned only by POST /tokens: the plaintext `mhk_...` secret is present
// exactly once at creation time and is never persisted or re-fetchable.
export interface ApiTokenCreated extends ApiToken {
  token: string;
}

const base = () => `${getApiUrl()}/api/v1/inspiration`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || e.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// Defensive unwrap for the array-shaped endpoints below (`listNotes`,
// `getActivity`, `getTagCounts`). The real backend returns a bare JSON array
// for each of these, but the declared `Promise<T[]>` return type is only a
// compile-time promise — nothing here validated it at runtime, so a response
// shaped differently (this is exactly what the e2e stub harness's generic
// `**/api/v1/**` catch-all returns: `{ success: true, data: [] }`, not an
// array) flowed straight through as an *object* wearing an array's type.
// `setNotes(rows)` in `InspirationPage` then put that object into state, and
// every consumer that unconditionally calls `.filter`/`.map` on `notes` (e.g.
// `NoteTimeline.tsx`) threw `TypeError: <obj>.filter is not a function` out
// of render — this was the actual mechanism behind the pre-existing "u is
// not iterable" crash the K1/K2 module-accent-probe reports flagged (that
// generic message is what the same class of bug produces when the caller
// iterates with `for...of` instead of `.filter`). Fixed by validating the
// shape here so a malformed payload degrades to `[]` instead of leaking a
// non-array value into React state.
//
// The degrade-to-[] is intentionally non-fatal, but it must not be silent
// either — `resourceService.ts`'s `checkDuplicatesBatch` and
// `workflowService.ts`'s `fetchTemplates` both log/throw on the same class
// of shape drift rather than swallowing it. `fnName` identifies the caller
// in the console so a real drift (vs. an intentionally-empty test stub) is
// visible in application logs, not just silently empty.
function toArray<T>(value: unknown, fnName: string): T[] {
  if (Array.isArray(value)) return value as T[];
  console.warn(`${fnName}: unexpected non-array response shape`, value);
  return [];
}

export async function listNotes(
  filters: NoteFilters,
  limit = 50,
  beforeId?: string,
): Promise<InspirationNote[]> {
  const params = new URLSearchParams();
  if (filters.date) params.set('date', filters.date);
  if (filters.tag) params.set('tag', filters.tag);
  if (filters.q) params.set('q', filters.q);
  // Truthiness on purpose (unlike `createNote`'s rating): 0 is the dropdown's
  // "Any rating" value, i.e. no filter — sending `min_rating=0` would ask the
  // backend to filter by `rating >= 0`, which is a different request that
  // happens to return the same rows.
  if (filters.min_rating) params.set('min_rating', String(filters.min_rating));
  params.set('limit', String(limit));
  if (beforeId) params.set('before_id', beforeId);
  const resp = await fetch(`${base()}/notes?${params.toString()}`, {
    headers: await getAuthHeaders(),
  });
  return toArray<InspirationNote>(await jsonOrThrow(resp), 'listNotes');
}

export async function createNote(
  contentMd: string,
  refHotspot?: RefHotspot,
  /** 0-5. Sent in this SAME request so an external client (the iOS Shortcut)
   *  never needs a follow-up PATCH. Omitted when undefined → DB default 0. */
  rating?: number,
): Promise<InspirationNote> {
  const body: Record<string, unknown> = { content_md: contentMd };
  if (refHotspot) body.ref_hotspot = refHotspot;
  // `!== undefined`, not truthiness: rating 0 is a real value to send.
  if (rating !== undefined) body.rating = rating;
  const resp = await fetch(`${base()}/notes`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return jsonOrThrow(resp);
}

export async function updateNote(
  id: string,
  patch: {
    content_md?: string;
    pinned?: boolean;
    rating?: number;
  },
): Promise<InspirationNote> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow(resp);
}

export async function deleteNote(id: string): Promise<void> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
}

export async function getActivity(
  dateFrom: string,
  dateTo: string,
): Promise<{ day: string; cnt: number }[]> {
  const resp = await fetch(
    `${base()}/notes/activity?date_from=${dateFrom}&date_to=${dateTo}`,
    { headers: await getAuthHeaders() },
  );
  return toArray<{ day: string; cnt: number }>(await jsonOrThrow(resp), 'getActivity');
}

export async function getTagCounts(): Promise<{ tag: string; cnt: number }[]> {
  const resp = await fetch(`${base()}/notes/tags`, {
    headers: await getAuthHeaders(),
  });
  return toArray<{ tag: string; cnt: number }>(await jsonOrThrow(resp), 'getTagCounts');
}

export async function uploadAttachment(
  noteId: string,
  file: File,
): Promise<NoteAttachment> {
  const headers = { ...(await getAuthHeaders()) } as Record<string, string>;
  delete headers['Content-Type']; // browser sets the multipart boundary
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${base()}/attachments/upload?note_id=${noteId}`, {
    method: 'POST',
    headers,
    body: form,
  });
  return jsonOrThrow(resp);
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const resp = await fetch(`${base()}/attachments/${attachmentId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    // Read `detail` like deleteNote above: the backend distinguishes 404
    // "attachment not found" from 502 "attachment deletion failed", and the
    // Composer puts this message straight into a user-facing toast. The
    // no-body fallback is `{}` (not deleteNote's 'Delete failed' string) so a
    // bodyless response degrades to the status code, which says more.
    const e = await resp.json().catch(() => ({}) as { detail?: string });
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
}

/**
 * Build the signed-get URL for an attachment, including a `?token=` query
 * param so browser-native loads (`<img>`/`<video>`/`<audio>` src, `<a href>`
 * downloads) can authenticate — those elements never send an Authorization
 * header. Backend accepts this token via the same media/JWT dual-channel as
 * resources_crud_router's `/file` route (see inspiration_router.get_attachment).
 *
 * `token` must be the short-lived, independently-revocable media token
 * (AuthContext's `mediaToken`, minted by POST /auth/media-token) — mirrors
 * resourceService's getResourceMediaUrl/getResourceFileUrl/getResourceCoverUrl,
 * which all take the media token as a caller-supplied parameter rather than
 * fetching it themselves. Do NOT pass the long-lived Supabase session JWT
 * here: it would land in nginx/app logs, browser history, and Referer
 * headers, and can't be revoked independently of the whole session.
 */
export function attachmentUrlWithToken(attachmentId: string, token?: string): string {
  const url = `${base()}/attachments/${attachmentId}`;
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}

// ── Personal Access Tokens (PAT) — external write access to the library ──

export async function listTokens(): Promise<ApiToken[]> {
  const resp = await fetch(`${base()}/tokens`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function createToken(name: string): Promise<ApiTokenCreated> {
  const resp = await fetch(`${base()}/tokens`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return jsonOrThrow(resp);
}

export async function revokeToken(id: string): Promise<void> {
  const resp = await fetch(`${base()}/tokens/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Revoke failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
}
