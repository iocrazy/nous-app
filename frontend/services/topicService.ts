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
  tags: string[];
  category?: string | null;
  media_url?: string | null;
  cover_url?: string | null;
  captured_at?: string | null;
  heat?: number | null;
  best_rank?: number | null;
  content_original?: string | null;
  content_translated?: string | null;
  is_read?: boolean;
  is_saved?: boolean;
  is_hidden?: boolean;
}

export type HotspotView = 'all' | 'saved' | 'hidden';

export interface HotspotStatePatch {
  is_read?: boolean;
  is_saved?: boolean;
  is_hidden?: boolean;
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
): Promise<Hotspot[]> {
  const params = new URLSearchParams();
  if (view !== 'all') params.set('view', view);
  // Server-side search: the query MUST reach the backend WHERE clause, never
  // filter client-side over an already-truncated page.
  const term = (q || '').trim();
  if (term) params.set('q', term);
  else if (day) params.set('day', day); // day only narrows plain browsing
  if (category && category !== 'all') params.set('category', category);
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
}

export async function getSourceHealth(): Promise<SourceHealth[]> {
  const resp = await fetch(`${base()}/sources/health`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).sources as SourceHealth[];
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
