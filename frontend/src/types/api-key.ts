// frontend/src/types/api-key.ts

/**
 * API 密钥类型定义
 */

export interface ApiKeyScope {
  scope: string
  name: string
  description: string
  category: string
}

export interface ApiKey {
  id: number
  key_id: string
  key_prefix: string
  name: string
  description: string | null
  scopes: string[]
  status: 'active' | 'revoked' | 'expired'
  expires_at: string | null
  last_used_at: string | null
  usage_count: number
  rate_limit: number | null
  created_at: string
  updated_at: string
}

export interface ApiKeyCreateRequest {
  name: string
  description?: string
  scopes: string[]
  expires_at?: string
  rate_limit?: number
}

export interface ApiKeyCreateResponse {
  success: boolean
  message: string
  id: number
  key_id: string
  key_prefix: string
  name: string
  description: string | null
  scopes: string[]
  status: string
  expires_at: string | null
  rate_limit: number | null
  created_at: string
  updated_at: string
  secret_key: string // 仅创建时返回
}

export interface ApiKeyUpdateRequest {
  name?: string
  description?: string
  scopes?: string[]
  status?: 'active' | 'revoked'
}

export interface ApiKeyListResponse {
  success: boolean
  count: number
  keys: ApiKey[]
}

export interface ApiKeyScopesResponse {
  success: boolean
  scopes: ApiKeyScope[]
}
