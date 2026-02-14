/**
 * API Key Service
 *
 * Handles CRUD operations for API keys via the backend API.
 */

import { getAuthHeaders } from './parserService';

// API 配置
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// Backend API response types
export interface ApiKeyScopeInfo {
  scope: string;
  name: string;
  description: string;
  category: string;
}

export interface ApiKeyResponse {
  id: number;
  key_id: string;
  key_prefix: string;
  key_value?: string | null;
  name: string;
  description: string | null;
  scopes: string[];
  status: string;
  expires_at: string | null;
  last_used_at: string | null;
  usage_count: number;
  rate_limit: number | null;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyCreateResponse extends ApiKeyResponse {
  success: boolean;
  message: string;
  secret_key: string;  // Full key, only returned once!
}

export interface ApiKeyListResponse {
  success: boolean;
  count: number;
  keys: ApiKeyResponse[];
}

export interface ApiKeyScopesResponse {
  success: boolean;
  scopes: ApiKeyScopeInfo[];
}

export interface CreateApiKeyRequest {
  name: string;
  description?: string;
  scopes: string[];
  expires_at?: string;  // ISO date string
  rate_limit?: number;
}

export interface UpdateApiKeyRequest {
  name?: string;
  description?: string;
  scopes?: string[];
  status?: 'active' | 'revoked';
}

/**
 * Get available API scopes
 */
export async function getAvailableScopes(): Promise<ApiKeyScopeInfo[]> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys/scopes`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to get scopes: ${response.statusText}`);
  }

  const data: ApiKeyScopesResponse = await response.json();
  return data.scopes;
}

/**
 * List all API keys for the current user
 */
export async function listApiKeys(includeRevoked: boolean = false): Promise<ApiKeyResponse[]> {
  const url = new URL(`${getApiUrl()}/api/v1/api-keys`);
  url.searchParams.set('include_revoked', includeRevoked.toString());

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to list keys: ${response.statusText}`);
  }

  const data: ApiKeyListResponse = await response.json();
  return data.keys;
}

/**
 * Create a new API key
 * IMPORTANT: The secret_key is only returned once in the response!
 */
export async function createApiKey(request: CreateApiKeyRequest): Promise<ApiKeyCreateResponse> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create key: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Get a specific API key by key_id
 */
export async function getApiKey(keyId: string): Promise<ApiKeyResponse> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys/${keyId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to get key: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Update an API key
 */
export async function updateApiKey(keyId: string, request: UpdateApiKeyRequest): Promise<ApiKeyResponse> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys/${keyId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update key: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Delete an API key (hard delete)
 */
export async function deleteApiKey(keyId: string): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys/${keyId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to delete key: ${response.statusText}`);
  }
}

/**
 * Revoke an API key (soft delete)
 */
export async function revokeApiKey(keyId: string): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/v1/api-keys/${keyId}/revoke`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to revoke key: ${response.statusText}`);
  }
}
