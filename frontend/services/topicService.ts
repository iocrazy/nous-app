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
}

const base = () => `${getApiUrl()}/api/v1/topics`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function getHotspots(day?: string, category?: string): Promise<Hotspot[]> {
  const params = new URLSearchParams();
  if (day) params.set('day', day);
  if (category && category !== 'all') params.set('category', category);
  const resp = await fetch(`${base()}?${params.toString()}`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).hotspots as Hotspot[];
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
