import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { unwrapResponse } from '../utils/apiHelpers';
import { Skill } from '../types';

const CACHE_TTL_MS = 30_000;
let _cache: { data: Skill[]; ts: number; projectId?: string } | null = null;

function isCacheValid(projectId?: string): boolean {
  if (!_cache) return false;
  if (_cache.projectId !== projectId) return false;
  return Date.now() - _cache.ts < CACHE_TTL_MS;
}

export function invalidateSkillsCache(): void {
  _cache = null;
}

export async function fetchSkills(
  projectId?: string,
  category?: string,
): Promise<Skill[]> {
  if (!category && isCacheValid(projectId)) return _cache!.data;

  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  if (category) params.set('category', category);
  const res = await fetch(`${getApiUrl()}/api/v1/skills?${params}`, { headers });
  const data = await unwrapResponse<Skill[]>(res);

  if (!category) {
    _cache = { data, ts: Date.now(), projectId };
  }
  return data;
}

export async function fetchSkillDetail(id: string): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills/${id}`, { headers });
  return unwrapResponse<Skill>(res);
}

export async function createSkill(data: {
  name: string;
  description?: string;
  content_md: string;
  category?: string;
  icon?: string;
  output_format?: string;
  trigger_keywords?: string[];
  project_id?: string;
  is_public?: boolean;
}): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  invalidateSkillsCache();
  return unwrapResponse<Skill>(res);
}

export async function updateSkill(
  id: string,
  data: Partial<Pick<Skill, 'name' | 'description' | 'content_md' | 'category' | 'icon' | 'output_format' | 'trigger_keywords' | 'project_id' | 'is_public' | 'status'>>,
): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills/${id}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  invalidateSkillsCache();
  return unwrapResponse<Skill>(res);
}

export async function deleteSkill(id: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/skills/${id}`, {
    method: 'DELETE',
    headers,
  });
  invalidateSkillsCache();
}
