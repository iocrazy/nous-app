/**
 * Unit tests for sharesService — covers the success/failure envelope
 * unwrap behavior across the shares CRUD + public accessShare path.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  accessShare,
  cancelShare,
  createShare,
  deleteSharePermanent,
  fetchShares,
  getShare,
  updateShare,
} from './sharesService';

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

describe('createShare', () => {
  it('POSTs body and unwraps { data }', async () => {
    const spy = stubJson({
      success: true,
      data: { id: 's1', share_code: 'abc', share_type: 'review' },
    });
    const result = await createShare({
      resource_id: 'r1',
      share_type: 'review',
      share_name: 'My Share',
    });
    expect(result.id).toBe('s1');
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string).share_name).toBe('My Share');
  });

  it('throws the backend message on success=false', async () => {
    stubJson({ success: false, message: 'quota exceeded' });
    await expect(
      createShare({ share_type: 'review', share_name: 'x' }),
    ).rejects.toThrow('quota exceeded');
  });
});

describe('fetchShares', () => {
  it('threads all filters as query params', async () => {
    const spy = stubJson({ success: true, data: [] });
    await fetchShares({
      resource_id: 'r1',
      status: 'active',
      team_id: 't1',
      limit: 10,
      offset: 20,
    });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('resource_id=r1');
    expect(url).toContain('status=active');
    expect(url).toContain('team_id=t1');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=20');
  });

  it('unwraps data array', async () => {
    stubJson({
      success: true,
      data: [
        { id: 's1', share_code: 'a' },
        { id: 's2', share_code: 'b' },
      ],
    });
    const result = await fetchShares();
    expect(result).toHaveLength(2);
  });
});

describe('getShare', () => {
  it('GETs by id', async () => {
    const spy = stubJson({
      success: true,
      data: { id: 's1', share_code: 'abc' },
    });
    const result = await getShare('s1');
    expect(result.id).toBe('s1');
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/shares/s1');
  });
});

describe('updateShare', () => {
  it('PUTs partial updates', async () => {
    const spy = stubJson({
      success: true,
      data: { id: 's1', share_name: 'Renamed' },
    });
    await updateShare('s1', { share_name: 'Renamed', allow_download: true });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PUT');
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ share_name: 'Renamed', allow_download: true });
  });
});

describe('cancel / deletePermanent', () => {
  it('cancelShare DELETEs and unwraps envelope', async () => {
    const spy = stubJson({ success: true, data: null });
    await cancelShare('s1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('cancelShare throws on success=false', async () => {
    stubJson({ success: false, message: 'already cancelled' });
    await expect(cancelShare('s1')).rejects.toThrow('already cancelled');
  });

  it('deleteSharePermanent DELETEs the permanent sub-URL', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
      text: async () => '',
      json: async () => undefined,
    } as unknown as Response);
    await deleteSharePermanent('s1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/shares/s1/permanent',
    );
  });
});

describe('accessShare (public)', () => {
  it('POSTs password to the share-code URL', async () => {
    const spy = stubJson({
      success: true,
      data: { id: 's1', share_code: 'abc' },
    });
    await accessShare('abc', 'secret');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/shares/code/abc');
    expect((init as RequestInit).method).toBe('POST');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toEqual({ password: 'secret' });
  });

  it('accessShare sends password: null when omitted', async () => {
    const spy = stubJson({ success: true, data: { id: 's1' } });
    await accessShare('abc');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ password: null });
  });

  it('accessShare surfaces the success=false message', async () => {
    stubJson({ success: false, message: 'bad password' });
    await expect(accessShare('abc', 'wrong')).rejects.toThrow(
      'bad password',
    );
  });
});
