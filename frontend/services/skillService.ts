import { apiClient } from './apiClient';
import { Skill } from '../types';

interface Envelope<T> {
  success?: boolean;
  data: T;
}

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

  const result = await apiClient.get<Envelope<Skill[]>>('/api/v1/skills', {
    query: { project_id: projectId, category },
  });
  const data = result.data;

  if (!category) {
    _cache = { data, ts: Date.now(), projectId };
  }
  return data;
}

export async function fetchSkillDetail(id: string): Promise<Skill> {
  const result = await apiClient.get<Envelope<Skill>>(`/api/v1/skills/${id}`);
  return result.data;
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
  const result = await apiClient.post<Envelope<Skill>>('/api/v1/skills', data);
  invalidateSkillsCache();
  return result.data;
}

export async function updateSkill(
  id: string,
  data: Partial<
    Pick<
      Skill,
      | 'name'
      | 'description'
      | 'content_md'
      | 'category'
      | 'icon'
      | 'output_format'
      | 'trigger_keywords'
      | 'project_id'
      | 'is_public'
      | 'status'
    >
  >,
): Promise<Skill> {
  const result = await apiClient.patch<Envelope<Skill>>(
    `/api/v1/skills/${id}`,
    data,
  );
  invalidateSkillsCache();
  return result.data;
}

export async function deleteSkill(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/skills/${id}`);
  invalidateSkillsCache();
}
