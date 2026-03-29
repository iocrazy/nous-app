import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { unwrapResponse } from '../utils/apiHelpers';
import { StyleTemplate } from '../types';

export async function fetchStyleTemplates(
  category?: string,
): Promise<StyleTemplate[]> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  const res = await fetch(`${getApiUrl()}/api/v1/style-templates?${params}`, { headers });
  return unwrapResponse<StyleTemplate[]>(res);
}

export async function createStyleTemplate(data: {
  name: string;
  description?: string;
  prompt_content: string;
  category?: string;
  is_public?: boolean;
}): Promise<StyleTemplate> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/style-templates`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<StyleTemplate>(res);
}

export async function updateStyleTemplate(
  id: string,
  data: Partial<Pick<StyleTemplate, 'name' | 'description' | 'prompt_content' | 'category' | 'is_public'>>,
): Promise<StyleTemplate> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/style-templates/${id}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<StyleTemplate>(res);
}

export async function deleteStyleTemplate(id: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/style-templates/${id}`, {
    method: 'DELETE',
    headers,
  });
}
