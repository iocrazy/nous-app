// frontend/services/tempTtlService.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { tempTtlService } from './tempTtlService';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer t' }),
}));

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

describe('tempTtlService', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getChatTempTtl reads /library/temp-ttl', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ttl_days: 30 }), { status: 200 }),
      );
    const result = await tempTtlService.getChatTempTtl('personal', 'u1');
    expect(result).toEqual({ ttl_days: 30 });
    expect(fetchMock.mock.calls[0][0]).toContain(
      '/library/temp-ttl?scope_type=personal&scope_id=u1',
    );
  });

  it('setChatTempTtl PUTs the body', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ttl_days: 7 }), { status: 200 }),
      );
    await tempTtlService.setChatTempTtl('team', '42', 7);
    const [, init] = fetchMock.mock.calls[0];
    expect((init as RequestInit).method).toBe('PUT');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      scope_type: 'team',
      scope_id: '42',
      ttl_days: 7,
    });
  });

  it('promoteResource POSTs to /move with scope context', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true, data: {} }), { status: 200 }),
      );
    await tempTtlService.promoteResource('res-1', {
      folderId: null,
      scopeType: 'personal',
      scopeId: 'u1',
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/resources/res-1/move');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      folder_id: null,
      scope_type: 'personal',
      scope_id: 'u1',
    });
  });

  it('promoteResource forwards a non-null folder_id', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true, data: {} }), { status: 200 }),
      );
    await tempTtlService.promoteResource('res-1', {
      folderId: 'f-target',
      scopeType: 'team',
      scopeId: '42',
    });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      folder_id: 'f-target',
      scope_type: 'team',
      scope_id: '42',
    });
  });
});
