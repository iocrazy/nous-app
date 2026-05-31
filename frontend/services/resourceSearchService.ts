import { getAuthHeaders } from './parserService';
import type { ResourceSearchResponse } from '../types';

const API = import.meta.env.VITE_API_URL || '';

export async function searchResources(params: {
  q: string;
  kinds: string;
  limit?: number;
  teamId?: string;
  signal?: AbortSignal;
}): Promise<ResourceSearchResponse> {
  const headers = await getAuthHeaders();
  const u = new URL(`${API}/api/v1/resources/search`, window.location.origin);
  u.searchParams.set('q', params.q);
  if (params.kinds) u.searchParams.set('kinds', params.kinds);
  if (params.limit) u.searchParams.set('limit', String(params.limit));
  if (params.teamId) u.searchParams.set('team_id', params.teamId);
  const res = await fetch(u.toString(), { headers, signal: params.signal });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}
