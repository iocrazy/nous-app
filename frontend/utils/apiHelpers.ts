import { getApiUrl } from './apiConfig';
import { getAuthHeaders } from '../services/parserService';

export async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export async function unwrapResponse<T>(res: Response): Promise<T> {
  const body = await handleResponse<{ success: boolean; data: T }>(res);
  return body.data;
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}${path}`, {
    ...options,
    headers: { ...headers, ...options?.headers },
  });
  return unwrapResponse<T>(res);
}

export async function apiPost<T>(
  path: string,
  body: unknown,
): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
