/**
 * Unit tests for reviewCommentsService — share-code URL shapes, the share
 * grant on the query, and the real wire shape of a comment.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createShareComment,
  fetchShareComments,
} from './reviewCommentsService';

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

// `GET /shares/code/{code}/comments` item as the backend sends it: Snowflake
// ids are JSON numbers, `timecode` is seconds (float).
const COMMENT = {
  id: 339710259795355,
  content: 'hi',
  timecode: 12.5,
  frame_number: null,
  status: 'open',
  author_id: '00000000-0000-0000-0000-000000000042',
  parent_id: null,
  created_at: '2026-09-24T01:02:03.456789+00:00',
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('reviewCommentsService', () => {
  it('fetchShareComments unwraps data array', async () => {
    stubJson({ success: true, data: [COMMENT] });
    const rows = await fetchShareComments('SHARE1');
    expect(rows).toEqual([COMMENT]);
  });

  it('fetchShareComments sends the share grant as share_token', async () => {
    const spy = stubJson({ success: true, data: [] });
    await fetchShareComments('SHARE1', 'sg1.1.2.abc');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/shares/code/SHARE1/comments?share_token=sg1.1.2.abc',
    );
  });

  it('fetchShareComments surfaces the password refusal', async () => {
    stubJson(
      {
        success: false,
        error: 'Password required',
        code: 'http_401',
        request_id: 'r1',
        details: null,
      },
      401,
    );
    await expect(fetchShareComments('SHARE1')).rejects.toThrow('Password required');
  });

  it('createShareComment POSTs content + timecode (null by default)', async () => {
    const spy = stubJson({ success: true, data: { ...COMMENT, resource_id: 1 } });
    await createShareComment('SHARE1', { content: 'hi' });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/shares/code/SHARE1/comments');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toEqual({ content: 'hi', timecode: null });
  });

  it('createShareComment passes timecode and the share grant', async () => {
    const spy = stubJson({ success: true, data: { ...COMMENT, resource_id: 1 } });
    await createShareComment('SHARE1', { content: 'hi', timecode: 42 }, 'sg1.x');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe(
      'https://api.test/api/v1/shares/code/SHARE1/comments?share_token=sg1.x',
    );
    expect(JSON.parse((init as RequestInit).body as string).timecode).toBe(42);
  });
});
