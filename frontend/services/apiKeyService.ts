/**
 * API Key Service
 *
 * Handles CRUD operations for API keys via the backend API.
 */

import { apiClient } from './apiClient';

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
  key_value?: string | null; // Masked display form; full key revealed only once at creation
  key_value_set?: boolean;
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
  secret_key: string; // Full key, only returned once!
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

export interface ApiKeyRevealResponse {
  key: string; // Full plaintext key — copy-only, never render into the DOM
}

export interface CreateApiKeyRequest {
  name: string;
  description?: string;
  scopes: string[];
  expires_at?: string; // ISO date string
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
  const data = await apiClient.get<ApiKeyScopesResponse>(
    '/api/v1/api-keys/scopes',
  );
  return data.scopes;
}

/**
 * List all API keys for the current user
 */
export async function listApiKeys(
  includeRevoked: boolean = false,
): Promise<ApiKeyResponse[]> {
  const data = await apiClient.get<ApiKeyListResponse>('/api/v1/api-keys', {
    query: { include_revoked: includeRevoked },
  });
  return data.keys;
}

/**
 * Create a new API key
 * IMPORTANT: The secret_key is only returned once in the response!
 */
export async function createApiKey(
  request: CreateApiKeyRequest,
): Promise<ApiKeyCreateResponse> {
  return apiClient.post<ApiKeyCreateResponse>('/api/v1/api-keys', request);
}

/**
 * Get a specific API key by key_id
 */
export async function getApiKey(keyId: string): Promise<ApiKeyResponse> {
  return apiClient.get<ApiKeyResponse>(`/api/v1/api-keys/${keyId}`);
}

/**
 * Update an API key
 */
export async function updateApiKey(
  keyId: string,
  request: UpdateApiKeyRequest,
): Promise<ApiKeyResponse> {
  return apiClient.patch<ApiKeyResponse>(
    `/api/v1/api-keys/${keyId}`,
    request,
  );
}

/**
 * Delete an API key (hard delete)
 */
export async function deleteApiKey(keyId: string): Promise<void> {
  await apiClient.delete(`/api/v1/api-keys/${keyId}`);
}

/**
 * Revoke an API key (soft delete)
 */
export async function revokeApiKey(keyId: string): Promise<void> {
  await apiClient.post(`/api/v1/api-keys/${keyId}/revoke`);
}

/**
 * Reveal the FULL plaintext key for copy-to-clipboard (owner-only, audited).
 * The backend decrypts the encrypt-at-rest value; callers must copy the result
 * to the clipboard and never render it into the DOM.
 */
export async function revealApiKey(keyId: string): Promise<string> {
  const data = await apiClient.post<ApiKeyRevealResponse>(
    `/api/v1/api-keys/${keyId}/reveal`,
  );
  return data.key;
}
