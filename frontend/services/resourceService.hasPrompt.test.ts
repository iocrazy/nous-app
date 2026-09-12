/**
 * Unit tests for the "Has Prompt" AI-filter chip's server-side wiring on the
 * Resources/My Uploads view (fetchResourcesPaginated → PostgREST path /
 * fetchResourcesViaRpc).
 *
 * Post-review fix (I2): ai_has_prompt no longer forces the RPC path on its
 * own. It used to (see git history), which silently dropped `search` /
 * `flatten` — search_scope_resources has no p_search/p_flatten param, so
 * typing a filename then checking "Has Prompt" returned every prompt-bearing
 * resource with the search term ignored. The 4-column OR + jsonb-literal
 * predicate (HAS_PROMPT_OR_EXPRESSION, exported from resourceService.ts) IS
 * expressible via the PostgREST query builder — verified separately against
 * a live PostgREST v14.8 instance (see PR notes / migration 401 comment).
 * Only `tag_ids` still forces the RPC path (no resource_id-intersection
 * alternative at scale); when both tags AND has_prompt are active, the RPC
 * (search_scope_resources, mig 401) still carries p_has_prompt so that
 * combination stays correct.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// Capture every supabase.rpc call.
const rpcMock = vi.fn();
let rpcReturn: { data: unknown; error: unknown };

// Capture every supabase.from(...) chained call (method name + args) so the
// PostgREST-path test can assert the has_prompt `.or()` fragment was applied
// without needing a real network layer. Every chained method returns the
// same thenable proxy; `await`-ing it resolves to `fromReturn`.
type RecordedCall = { method: string; args: unknown[] };
let fromCalls: RecordedCall[];
let fromReturn: { data: unknown; error: unknown; count?: number | null };

function makeChainable(recorder: RecordedCall[]): unknown {
  const proxy: unknown = new Proxy(
    {},
    {
      get(_target, prop: string) {
        if (prop === 'then') {
          return (resolve: (v: unknown) => void) => resolve(fromReturn);
        }
        return (...args: unknown[]) => {
          recorder.push({ method: prop, args });
          return proxy;
        };
      },
    },
  );
  return proxy;
}

vi.mock('../supabaseClient', () => ({
  supabase: {
    rpc: (...args: unknown[]) => {
      rpcMock(...args);
      const obj: Record<string, unknown> = {
        abortSignal: () => obj,
        then: (resolve: (v: unknown) => void) => resolve(rpcReturn),
      };
      return obj;
    },
    from: (table: string) => {
      fromCalls.push({ method: 'from', args: [table] });
      return makeChainable(fromCalls);
    },
  },
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { fetchResourcesPaginated, HAS_PROMPT_OR_EXPRESSION } from './resourceService';

beforeEach(() => {
  rpcMock.mockClear();
  rpcReturn = { data: { rows: [], total_count: null }, error: null };
  fromCalls = [];
  fromReturn = { data: [], error: null, count: 0 };
});

describe('fetchResourcesPaginated — ai_has_prompt routing', () => {
  it('stays on the PostgREST path (no RPC call) when ai_has_prompt=true and no tag_ids', async () => {
    await fetchResourcesPaginated(
      { isPersonal: true, scopeId: 'user-1', ai_has_prompt: true },
      null,
      40,
    );
    expect(rpcMock).not.toHaveBeenCalled();
  });

  it('applies the has_prompt OR-predicate on the resources embed via the query builder', async () => {
    await fetchResourcesPaginated(
      { isPersonal: true, scopeId: 'user-1', ai_has_prompt: true },
      null,
      40,
    );
    const orCalls = fromCalls.filter((c) => c.method === 'or');
    const hasPromptCall = orCalls.find(
      (c) => c.args[0] === HAS_PROMPT_OR_EXPRESSION,
    );
    expect(hasPromptCall).toBeDefined();
    expect(hasPromptCall?.args[1]).toEqual({ referencedTable: 'resources' });
  });

  it('does NOT drop `search` when combined with ai_has_prompt (I2 regression check)', async () => {
    await fetchResourcesPaginated(
      {
        isPersonal: true,
        scopeId: 'user-1',
        ai_has_prompt: true,
        search: 'my-file',
      },
      null,
      40,
    );
    // What matters is that BOTH constraints reach the database, not which of
    // the two code paths carries them. Since migration 464 a keyword routes to
    // the RPC (only it can match a tag name), so this asserts the RPC received
    // the term and the has_prompt flag together. Asserting the old PostgREST
    // `.or()` here would have been pinning the route rather than the promise.
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const args = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(args.p_search).toBe('my-file');
    expect(args.p_has_prompt).toBe(true);
  });

  it('sends the search scope so a tag-name match is reachable', async () => {
    await fetchResourcesPaginated(
      {
        isPersonal: true,
        scopeId: 'user-1',
        search: 'hanfu',
        search_fields: ['tags'],
      },
      null,
      40,
    );
    // The Tags checkbox used to do nothing: matching a tag name needs
    // resource_tags -> tags, which the PostgREST builder cannot express, and
    // the RPC had no search parameter at all.
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const args = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(args.p_search).toBe('hanfu');
    expect(args.p_search_fields).toEqual(['tags']);
  });

  it('omits the scope when every field is ticked, letting the RPC default apply', async () => {
    await fetchResourcesPaginated(
      { isPersonal: true, scopeId: 'user-1', search: 'x', search_fields: [] },
      null,
      40,
    );
    const args = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(args.p_search_fields).toBeNull();
  });

  it('carries the flattened folder set into the RPC path', async () => {
    // "Show child files" has to survive the switch to the RPC, or searching
    // inside a folder would silently stop looking at descendants.
    await fetchResourcesPaginated(
      {
        isPersonal: true,
        scopeId: 'user-1',
        search: 'x',
        flatten: true,
        flattenFolderIds: ['10', '11'],
      },
      null,
      40,
    );
    const args = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(args.p_flatten).toBe(true);
    expect(args.p_folder_ids).toEqual(['10', '11']);
  });

  it('routes through search_scope_resources when tag_ids is set (with p_has_prompt=null when absent)', async () => {
    await fetchResourcesPaginated(
      { isPersonal: true, scopeId: 'user-1', tag_ids: ['tag-a'] },
      null,
      40,
    );
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const [name, params] = rpcMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe('search_scope_resources');
    expect(params.p_has_prompt).toBeNull();
  });

  it('combines tag_ids + ai_has_prompt into a single RPC call carrying both', async () => {
    await fetchResourcesPaginated(
      {
        isPersonal: true,
        scopeId: 'user-1',
        tag_ids: ['tag-a'],
        ai_has_prompt: true,
      },
      null,
      40,
    );
    expect(rpcMock).toHaveBeenCalledTimes(1);
    const params = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(params.p_has_prompt).toBe(true);
    expect(params.p_tag_ids).toEqual(['tag-a']);
  });
});
