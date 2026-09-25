/**
 * Unit tests for sharesService — URL shapes, bodies, and the real wire shapes
 * of `/api/v1/shares` (Snowflake ids are JSON numbers; failures are non-2xx
 * `ErrorResponse` bodies, not `success:false` 200s).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  accessShare,
  cancelShare,
  createShare,
  deleteSharePermanent,
  fetchShares,
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

function errorBody(error: string, status: number) {
  return { success: false, error, code: `http_${status}`, request_id: 'r1', details: null };
}

const SHARE_ID = 339710259795355;

// An owner's share row as `_enrich_share` sends it: no `password`, a
// `has_password` flag and a computed `share_url`.
const SHARE_ROW = {
  id: SHARE_ID,
  share_type: 'review',
  shared_by: '00000000-0000-0000-0000-000000000042',
  share_name: 'My Share',
  share_code: 'AbCd1234',
  allow_download: true,
  view_count: 0,
  watermark: false,
  status: 'active',
  created_at: '2026-09-24T01:02:03.456789+00:00',
  expires_at: null,
  max_views: null,
  project_file_id: null,
  version_id: null,
  resource_id: 339710259795001,
  folder_id: null,
  library_id: null,
  team_id: null,
  share_url: '/s/AbCd1234',
  has_password: false,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('createShare', () => {
  it('POSTs body and unwraps { data }', async () => {
    const spy = stubJson({ success: true, data: SHARE_ROW });
    const result = await createShare({
      resource_id: '339710259795001',
      share_type: 'review',
      share_name: 'My Share',
    });
    expect(result.id).toBe(SHARE_ID);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string).share_name).toBe('My Share');
  });

  it('surfaces the typed 404 for a target the caller cannot share', async () => {
    stubJson(
      {
        ...errorBody('Request failed', 404),
        details: { code: 'not_found_or_out_of_scope', message: 'Share target not found' },
      },
      404,
    );
    await expect(
      createShare({ share_type: 'review', share_name: 'x', resource_id: '1' }),
    ).rejects.toMatchObject({
      status: 404,
      details: { code: 'not_found_or_out_of_scope' },
    });
  });
});

describe('fetchShares', () => {
  it('threads the filters the backend reads as query params', async () => {
    const spy = stubJson({ success: true, data: [], count: 0 });
    await fetchShares({
      share_type: 'review',
      status: 'active',
      team_id: 'personal',
      limit: 10,
      offset: 20,
    });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('share_type=review');
    expect(url).toContain('status=active');
    expect(url).toContain('team_id=personal');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=20');
  });

  it('unwraps data array', async () => {
    stubJson({
      success: true,
      data: [SHARE_ROW, { ...SHARE_ROW, id: SHARE_ID + 1 }],
      count: 2,
    });
    const result = await fetchShares();
    expect(result).toHaveLength(2);
  });
});

describe('cancel / deletePermanent', () => {
  it('cancelShare DELETEs and returns the new status (no data key)', async () => {
    const spy = stubJson({ success: true, message: 'Share inactive', status: 'inactive' });
    expect(await cancelShare(SHARE_ID)).toBe('inactive');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('cancelShare throws on the typed 404', async () => {
    stubJson(errorBody('Request failed', 404), 404);
    await expect(cancelShare(SHARE_ID)).rejects.toMatchObject({ status: 404 });
  });

  it('deleteSharePermanent DELETEs the permanent sub-URL', async () => {
    const spy = stubJson({ success: true, message: 'Share deleted' });
    await deleteSharePermanent(SHARE_ID);
    expect(spy.mock.calls[0][0]).toBe(
      `https://api.test/api/v1/shares/${SHARE_ID}/permanent`,
    );
  });
});

describe('accessShare (public)', () => {
  const VISITOR = {
    id: SHARE_ID,
    share_type: 'link',
    share_name: 'My Share',
    share_code: 'abc',
    allow_download: true,
    watermark: false,
    view_count: 1,
    resource_id: 339710259795001,
    project_file_id: null,
    folder_id: null,
    version_id: null,
    created_at: '2026-09-24T01:02:03.456789+00:00',
    access_token: 'sg1.339710259795355.1790000000.abcdef',
  };

  it('POSTs password to the share-code URL and returns the grant', async () => {
    const spy = stubJson({ success: true, data: VISITOR });
    const result = await accessShare('abc', 'secret');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/shares/code/abc');
    expect((init as RequestInit).method).toBe('POST');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toEqual({ password: 'secret' });
    expect(result.access_token).toBe(VISITOR.access_token);
  });

  it('accessShare sends password: null when omitted', async () => {
    const spy = stubJson({ success: true, data: VISITOR });
    await accessShare('abc');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ password: null });
  });

  it('accessShare surfaces the backend refusal text (SharePage matches on it)', async () => {
    stubJson(errorBody('Incorrect password', 401), 401);
    await expect(accessShare('abc', 'wrong')).rejects.toThrow('Incorrect password');
  });
});
