// frontend/services/topicService.ts
import { getAuthHeaders, parseShareLink } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface Hotspot {
  id: string;
  title: string;
  url?: string | null;
  origin_url?: string | null;
  source_label?: string | null;
  summary?: string | null;
  ai_summary?: string | null;
  reason?: string | null;
  score?: number | null;
  /** Raw per-dimension scores (0..1) behind `score` — detail view only. */
  score_dims?: Record<string, number> | null;
  tags: string[];
  category?: string | null;
  media_url?: string | null;
  cover_url?: string | null;
  captured_at?: string | null;
  heat?: number | null;
  best_rank?: number | null;
  /** Cross-source cluster size: distinct platforms this topic appears on. */
  source_count?: number | null;
  /** The distinct member source labels behind source_count — for the
   *  "which platforms" hover tooltip. */
  source_names?: string[] | null;
  content_original?: string | null;
  content_translated?: string | null;
  is_read?: boolean;
  is_saved?: boolean;
  is_hidden?: boolean;
}

export type HotspotView = 'all' | 'featured' | 'foryou' | 'saved' | 'hidden';

export interface HotspotStatePatch {
  is_read?: boolean;
  is_saved?: boolean;
  is_hidden?: boolean;
}

export interface TopicInterest {
  interest_text: string;
  has_embedding: boolean;
}

const base = () => `${getApiUrl()}/api/v1/topics`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function getHotspots(
  day?: string,
  category?: string,
  q?: string,
  view: HotspotView = 'all',
  sources?: string[],
): Promise<Hotspot[]> {
  const params = new URLSearchParams();
  if (view !== 'all') params.set('view', view);
  // Server-side search: the query MUST reach the backend WHERE clause, never
  // filter client-side over an already-truncated page.
  const term = (q || '').trim();
  if (term) params.set('q', term);
  else if (day) params.set('day', day); // day only narrows plain browsing
  if (category && category !== 'all') params.set('category', category);
  // Source filter: narrow the feed to picked sources (intersected server-side
  // with the caller's visible allowlist). Empty/undefined = all visible.
  if (sources && sources.length > 0) params.set('source', sources.join(','));
  const resp = await fetch(`${base()}?${params.toString()}`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).hotspots as Hotspot[];
}

// Full hotspot incl. original/translated body for the detail panel.
export async function getHotspot(id: string): Promise<Hotspot> {
  const resp = await fetch(`${base()}/${id}`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).hotspot as Hotspot;
}

export async function setHotspotState(
  id: string,
  patch: HotspotStatePatch,
): Promise<HotspotStatePatch> {
  const resp = await fetch(`${base()}/${id}/state`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow(resp);
}

export async function getInterest(): Promise<TopicInterest> {
  const resp = await fetch(`${base()}/interest`, { headers: await getAuthHeaders() });
  const d = await jsonOrThrow(resp);
  return { interest_text: d.interest_text || '', has_embedding: !!d.has_embedding };
}

export async function setInterest(interest_text: string): Promise<TopicInterest> {
  const resp = await fetch(`${base()}/interest`, {
    method: 'PUT',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ interest_text }),
  });
  const d = await jsonOrThrow(resp);
  return { interest_text: d.interest_text || '', has_embedding: !!d.has_embedding };
}

export async function getHotspotDates(): Promise<string[]> {
  const resp = await fetch(`${base()}/dates`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).dates as string[];
}

export type SourceHealthStatus = 'ok' | 'degraded' | 'dead';

export interface SourceHealth {
  id: string;
  name: string;
  kind: string;
  category?: string | null;
  enabled: boolean;
  health: SourceHealthStatus;
  consecutive_failures: number;
  last_error?: string | null;
  last_fetched_at?: string | null;
  last_ok_at?: string | null;
  /** This caller created the source — they can delete it. */
  is_owner: boolean;
  /** This caller has closed the source (excluded from their feed). */
  is_hidden: boolean;
}

export type SourceKind = 'newsnow' | 'rss' | 'http_api' | 'custom';

export interface NewSourcePayload {
  kind: SourceKind;
  name: string;
  category?: string | null;
  config?: Record<string, unknown>;
}

export async function getSourceHealth(): Promise<SourceHealth[]> {
  const resp = await fetch(`${base()}/sources/health`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).sources as SourceHealth[];
}

// Add a user-owned source. Its hotspots are private to the caller.
export async function addSource(payload: NewSourcePayload): Promise<SourceHealth | null> {
  const resp = await fetch(`${base()}/sources`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return ((await jsonOrThrow(resp)).source ?? null) as SourceHealth | null;
}

// Delete a source the caller OWNS (stops collection). System sources reject (404).
export async function deleteSource(id: string): Promise<void> {
  const resp = await fetch(`${base()}/sources/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  await jsonOrThrow(resp);
}

// Close (hide=true) or re-open (hide=false) a source for this caller only.
export async function setSourceHidden(id: string, hide: boolean): Promise<void> {
  const resp = await fetch(`${base()}/sources/${id}/hide`, {
    method: hide ? 'POST' : 'DELETE',
    headers: await getAuthHeaders(),
  });
  await jsonOrThrow(resp);
}

// generate-script returns the script_ai outline (a list of chapter objects), not a string.
export async function generateScript(id: string): Promise<unknown> {
  const resp = await fetch(`${base()}/${id}/generate-script`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return (await jsonOrThrow(resp)).script;
}

// 解析下载: NO new backend endpoint — reuse the existing, battle-tested parser
// (parseShareLink → POST /api/v1/media/fetch). Only callable when a hotspot has media_url.
export async function parseDownload(mediaUrl: string): Promise<unknown> {
  return parseShareLink(mediaUrl, { video_bool: true, cover_bool: true });
}
