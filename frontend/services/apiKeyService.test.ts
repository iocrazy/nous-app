/**
 * Unit tests for apiKeyService — pins URL/method/body shapes and unwraps
 * the list/scopes envelope responses.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createApiKey,
  deleteApiKey,
  getApiKey,
  getAvailableScopes,
  listApiKeys,
  revokeApiKey,
  updateApiKey,
} from './apiKeyService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown, status: number = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('apiKeyService', () => {
  it('getAvailableScopes unwraps scopes array', async () => {
    stubJson({
      success: true,
      scopes: [
        { scope: 'read', name: 'Read', description: '', category: 'general' },
      ],
    });
    const scopes = await getAvailableScopes();
    expect(scopes).toHaveLength(1);
    expect(scopes[0].scope).toBe('read');
  });

  it('listApiKeys threads include_revoked query and unwraps keys', async () => {
    const spy = stubJson({
      success: true,
      count: 2,
      keys: [{ id: 1 }, { id: 2 }],
    });
    const keys = await listApiKeys(true);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('include_revoked=true');
    expect(keys).toHaveLength(2);
  });

  it('listApiKeys defaults include_revoked=false', async () => {
    const spy = stubJson({ success: true, count: 0, keys: [] });
    await listApiKeys();
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('include_revoked=false');
  });

  it('createApiKey POSTs payload and returns full response', async () => {
    const spy = stubJson({
      success: true,
      id: 1,
      key_id: 'k-1',
      key_prefix: 'sk_',
      name: 'test',
      description: null,
      scopes: ['read'],
      status: 'active',
      expires_at: null,
      last_used_at: null,
      usage_count: 0,
      rate_limit: null,
      created_at: '',
      updated_at: '',
      message: 'ok',
      secret_key: 'sk_xxx',
    });
    const result = await createApiKey({ name: 'test', scopes: ['read'] });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.name).toBe('test');
    expect(body.scopes).toEqual(['read']);
    expect(result.secret_key).toBe('sk_xxx');
  });

  it('getApiKey GETs /api/v1/api-keys/:id', async () => {
    const spy = stubJson({ id: 1, key_id: 'k-1' });
    await getApiKey('k-1');
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/api-keys/k-1');
  });

  it('updateApiKey PATCHes the key resource', async () => {
    const spy = stubJson({ id: 1 });
    await updateApiKey('k-1', { name: 'renamed' });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
    const body = JSON.parse(init.body as string);
    expect(body.name).toBe('renamed');
  });

  it('deleteApiKey issues DELETE', async () => {
    const spy = stubJson({});
    await deleteApiKey('k-1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('revokeApiKey POSTs /:id/revoke with no body', async () => {
    const spy = stubJson({});
    await revokeApiKey('k-1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/api-keys/k-1/revoke',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });
});
