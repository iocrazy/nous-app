/**
 * Unit tests for the workflowService `episodeId` threading (B2 T5-1).
 *
 * Pins the backward-compat命门: the 4 episode-aware endpoints must produce a
 * BYTE-IDENTICAL URL (no `episode_id` key) when `episodeId` is omitted, and
 * must append `episode_id=<value>` when it is given. apiClient's buildUrl
 * skips `undefined` query values, so `episode_id: episodeId || undefined`
 * is what makes the omitted case invisible.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  executeAdvance,
  fetchAdvancePreview,
  fetchProjectWorkflow,
  startEarlyNode,
} from './workflowService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubFetch(body: unknown, status: number = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('workflowService episode_id threading', () => {
  it('fetchProjectWorkflow omits episode_id when episodeId is undefined', async () => {
    const spy = stubFetch({ nodes: [] });
    await fetchProjectWorkflow('p-1');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe('https://api.test/api/v1/projects/p-1/workflow');
    expect(url).not.toContain('episode_id');
  });

  it('fetchProjectWorkflow appends episode_id when given', async () => {
    const spy = stubFetch({ nodes: [] });
    await fetchProjectWorkflow('p-1', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('episode_id=ep-9');
  });

  it('fetchAdvancePreview keeps only direction when episodeId omitted', async () => {
    const spy = stubFetch({});
    await fetchAdvancePreview('p-1', 'forward');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe(
      'https://api.test/api/v1/projects/p-1/advance-preview?direction=forward',
    );
    expect(url).not.toContain('episode_id');
  });

  it('fetchAdvancePreview appends episode_id alongside direction', async () => {
    const spy = stubFetch({});
    await fetchAdvancePreview('p-1', 'back', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('direction=back');
    expect(url).toContain('episode_id=ep-9');
  });

  it('executeAdvance keeps only direction when episodeId omitted', async () => {
    const spy = stubFetch({ data: {} });
    await executeAdvance('p-1', 'forward');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe(
      'https://api.test/api/v1/projects/p-1/advance?direction=forward',
    );
    expect(url).not.toContain('episode_id');
  });

  it('executeAdvance appends episode_id alongside direction', async () => {
    const spy = stubFetch({ data: {} });
    await executeAdvance('p-1', 'forward', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('direction=forward');
    expect(url).toContain('episode_id=ep-9');
  });

  it('startEarlyNode omits episode_id when episodeId undefined', async () => {
    const spy = stubFetch({ data: {} });
    await startEarlyNode('p-1', 'n-1');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe(
      'https://api.test/api/v1/projects/p-1/workflow/nodes/n-1/start-early',
    );
    expect(url).not.toContain('episode_id');
  });

  it('startEarlyNode appends episode_id when given', async () => {
    const spy = stubFetch({ data: {} });
    await startEarlyNode('p-1', 'n-1', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('episode_id=ep-9');
  });
});
