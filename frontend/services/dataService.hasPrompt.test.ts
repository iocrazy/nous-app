/**
 * Unit tests for the "Has Prompt" AI-filter chip's server-side wiring on the
 * Downloads view (fetchLibraryPaginated → fetchLibraryViaRpc).
 *
 * ai_has_prompt is NOT a status column (unlike ai_transcribed/ai_summarized/
 * ai_analyzed) — it's an OR across 4 prompt columns including a JSONB column
 * that must exclude the 'null'/'{}' literals. That predicate isn't
 * expressible via the PostgREST query builder used by applyLibraryFilters
 * (the no-tag path), so ai_has_prompt=true must force the RPC path
 * (rpc_downloads_library_search, mig 401 adds p_has_prompt) even with no
 * tag_ids.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const rpcMock = vi.fn();
let rpcReturn: { data: unknown; error: unknown };

vi.mock('../supabaseClient', () => ({
  isSupabaseConfigured: () => true,
  getSupabaseClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { user: { id: 'user-1' } } },
      }),
    },
    rpc: (...args: unknown[]) => {
      rpcMock(...args);
      const obj: Record<string, unknown> = {
        abortSignal: () => obj,
        then: (resolve: (v: unknown) => void) => resolve(rpcReturn),
      };
      return obj;
    },
  }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { fetchLibraryPaginated } from './dataService';

beforeEach(() => {
  rpcMock.mockClear();
  rpcReturn = { data: { rows: [], total_count: null }, error: null };
});

describe('fetchLibraryPaginated — ai_has_prompt routing', () => {
  it('routes through rpc_downloads_library_search when ai_has_prompt=true, even with no tag_ids', async () => {
    await fetchLibraryPaginated(0, 40, undefined, { ai_has_prompt: true }, null);
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const [name, params] = rpcMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe('rpc_downloads_library_search');
    expect(params.p_has_prompt).toBe(true);
  });

  it('passes p_has_prompt=null when ai_has_prompt is absent (tag-filtered path)', async () => {
    await fetchLibraryPaginated(0, 40, undefined, { tag_ids: ['tag-a'] }, null);
    const params = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(params.p_has_prompt).toBeNull();
  });

  it('combines with tag_ids without double-invoking the RPC', async () => {
    await fetchLibraryPaginated(
      0,
      40,
      undefined,
      { tag_ids: ['tag-a'], ai_has_prompt: true },
      null,
    );
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const params = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(params.p_has_prompt).toBe(true);
    expect(params.p_tag_ids).toEqual(['tag-a']);
  });
});
