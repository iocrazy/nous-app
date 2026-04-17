/**
 * Unit tests for reviewService — pins the /reviews/comments and
 * /reviews/status endpoint contract.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createComment,
  deleteComment,
  fetchCommentCount,
  fetchComments,
  fetchReviewStatuses,
  reopenComment,
  resolveComment,
  setReviewStatus,
  updateComment,
} from './reviewService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('fetchComments', () => {
  it('threads resource_id + version_id + status', async () => {
    const spy = stubJson({ data: [] });
    await fetchComments('r1', 'v1', 'open');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('resource_id=r1');
    expect(url).toContain('version_id=v1');
    expect(url).toContain('status=open');
  });

  it('returns [] when envelope.data is missing', async () => {
    stubJson({});
    expect(await fetchComments('r1')).toEqual([]);
  });
});

describe('createComment', () => {
  it('POSTs the full payload', async () => {
    const spy = stubJson({
      data: { id: 'c1', content: 'hi', resource_id: 'r1', status: 'open' },
    });
    const result = await createComment({
      resource_id: 'r1',
      content: 'hi',
      timecode: 12.5,
    });
    expect(result.content).toBe('hi');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({
      resource_id: 'r1',
      content: 'hi',
      timecode: 12.5,
    });
  });

  it('throws on empty envelope', async () => {
    stubJson({});
    await expect(
      createComment({ resource_id: 'r1', content: 'x' }),
    ).rejects.toThrow('Empty response from createComment');
  });
});

describe('updateComment / resolve / reopen / delete', () => {
  it('updateComment PATCHes the comment URL', async () => {
    const spy = stubJson({
      data: { id: 'c1', content: 'edited', status: 'open' },
    });
    await updateComment('c1', { content: 'edited' });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/reviews/comments/c1');
    expect((init as RequestInit).method).toBe('PATCH');
  });

  it('resolveComment POSTs to the resolve sub-URL', async () => {
    const spy = stubJson({ data: { id: 'c1', status: 'resolved' } });
    await resolveComment('c1');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe(
      'https://api.test/api/v1/reviews/comments/c1/resolve',
    );
    expect((init as RequestInit).method).toBe('POST');
  });

  it('reopenComment POSTs to the reopen sub-URL', async () => {
    const spy = stubJson({ data: { id: 'c1', status: 'open' } });
    await reopenComment('c1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/reviews/comments/c1/reopen',
    );
  });

  it('deleteComment DELETEs', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
      text: async () => '',
      json: async () => undefined,
    } as unknown as Response);

    await deleteComment('c1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('fetchCommentCount', () => {
  it('unwraps data.count', async () => {
    stubJson({ data: { count: 7 } });
    const count = await fetchCommentCount('r1');
    expect(count).toBe(7);
  });

  it('defaults to 0 when missing', async () => {
    stubJson({ data: {} });
    const count = await fetchCommentCount('r1');
    expect(count).toBe(0);
  });
});

describe('review status', () => {
  it('setReviewStatus POSTs resource_id + status', async () => {
    const spy = stubJson({
      data: { id: 's1', status: 'approved', resource_id: 'r1' },
    });
    const result = await setReviewStatus({
      resource_id: 'r1',
      status: 'approved',
      comment: 'LGTM',
    });
    expect(result.status).toBe('approved');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.resource_id).toBe('r1');
    expect(body.comment).toBe('LGTM');
  });

  it('fetchReviewStatuses returns [] on empty', async () => {
    stubJson({});
    expect(await fetchReviewStatuses('r1')).toEqual([]);
  });

  it('fetchReviewStatuses threads version_id', async () => {
    const spy = stubJson({ data: [] });
    await fetchReviewStatuses('r1', 'v1');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('resource_id=r1');
    expect(url).toContain('version_id=v1');
  });
});
