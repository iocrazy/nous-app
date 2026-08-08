/**
 * Unit tests for the workflowService `episodeId` threading (B2 T5-1, tightened
 * B6 PR-2 task 10).
 *
 * The 4 episode-aware endpoints (fetchProjectWorkflow / fetchAdvancePreview /
 * executeAdvance / startEarlyNode) now REQUIRE `episodeId` — the backend
 * dropped the legacy project-level (no episode_id) path and 422s a bare
 * request. Every call therefore always appends `episode_id=<value>` to the
 * URL; there is no longer an "omitted" case to pin (callers gate on a
 * resolved episode id before calling — see useProjectWorkflow / issueFlow /
 * WorkflowSection / WorkspaceStageBoard).
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
  it('fetchProjectWorkflow appends episode_id', async () => {
    const spy = stubFetch({ nodes: [] });
    await fetchProjectWorkflow('p-1', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe('https://api.test/api/v1/projects/p-1/workflow?episode_id=ep-9');
  });

  it('fetchAdvancePreview appends episode_id alongside direction', async () => {
    const spy = stubFetch({});
    await fetchAdvancePreview('p-1', 'back', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('direction=back');
    expect(url).toContain('episode_id=ep-9');
  });

  it('executeAdvance appends episode_id alongside direction', async () => {
    const spy = stubFetch({ data: {} });
    await executeAdvance('p-1', 'forward', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('direction=forward');
    expect(url).toContain('episode_id=ep-9');
  });

  it('startEarlyNode appends episode_id', async () => {
    const spy = stubFetch({ data: {} });
    await startEarlyNode('p-1', 'n-1', 'ep-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toBe(
      'https://api.test/api/v1/projects/p-1/workflow/nodes/n-1/start-early?episode_id=ep-9',
    );
  });
});
