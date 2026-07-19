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
  note_date: string;
  /** Beats M4 timeline anchor: script id (Snowflake string) or null. */
  anchor_script_id?: string | null;
  /** Whole-second offset on that script's beats timeline, or null. */
  anchor_sec?: number | null;
  created_at: string;
  updated_at: string;
  attachments: NoteAttachment[];
}

/** A memo pin's timeline anchor (both fields together). */
export interface NoteAnchor {
  scriptId: string;
  sec: number;
}

export interface NoteFilters {
  date?: string;
  tag?: string;
  q?: string;
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

export async function listNotes(
  filters: NoteFilters,
  limit = 50,
  beforeId?: string,
): Promise<InspirationNote[]> {
  const params = new URLSearchParams();
  if (filters.date) params.set('date', filters.date);
  if (filters.tag) params.set('tag', filters.tag);
  if (filters.q) params.set('q', filters.q);
  params.set('limit', String(limit));
  if (beforeId) params.set('before_id', beforeId);
  const resp = await fetch(`${base()}/notes?${params.toString()}`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function createNote(
  contentMd: string,
  refHotspot?: RefHotspot,
  anchor?: NoteAnchor,
): Promise<InspirationNote> {
  const body: Record<string, unknown> = { content_md: contentMd };
  if (refHotspot) body.ref_hotspot = refHotspot;
  if (anchor) {
    body.anchor_script_id = anchor.scriptId;
    body.anchor_sec = anchor.sec;
  }
  const resp = await fetch(`${base()}/notes`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return jsonOrThrow(resp);
}

/**
 * Every note this user has pinned to a script's beats timeline (any offset) —
 * the memo-rail query. A high limit fetches them all in one shot; the backing
 * partial index (mig 378) keeps it cheap.
 */
export async function listAnchoredNotes(scriptId: string): Promise<InspirationNote[]> {
  const params = new URLSearchParams({ anchor_script_id: scriptId, limit: '200' });
  const resp = await fetch(`${base()}/notes?${params.toString()}`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function updateNote(
  id: string,
  patch: {
    content_md?: string;
    pinned?: boolean;
    // Explicit null clears the anchor (un-pin); an absent key leaves it as-is.
    anchor_script_id?: string | null;
    anchor_sec?: number | null;
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
  return jsonOrThrow(resp);
}

export async function getTagCounts(): Promise<{ tag: string; cnt: number }[]> {
  const resp = await fetch(`${base()}/notes/tags`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
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
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
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
