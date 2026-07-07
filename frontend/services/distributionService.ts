import { SocialAccount } from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getApiUrl()}/api/v1/distribution${path}`, {
    ...init,
    headers: { ...(await getAuthHeaders()), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    throw new Error(`distribution api ${path} failed: ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const listAccounts = (): Promise<SocialAccount[]> =>
  request<{ accounts: SocialAccount[] }>('/accounts').then((r) => r.accounts);

export const connectAccount = (body: {
  platform: string;
  scope_type: 'user' | 'team';
  scope_id: string;
}): Promise<{ auth_url: string }> =>
  request<{ auth_url: string }>('/accounts/connect', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const refreshAccount = (id: string): Promise<SocialAccount> =>
  request<SocialAccount>(`/accounts/${id}/refresh`, { method: 'POST' });

export const deleteAccount = (id: string): Promise<void> =>
  request<void>(`/accounts/${id}`, { method: 'DELETE' });
