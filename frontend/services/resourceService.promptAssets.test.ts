/**
 * Unit tests for fetchPromptAssets — pins the Supabase query shape used by
 * the canvas Library picker (spec 2026-07-26-asset-prompt-management, Phase 2
 * Task 2): prompt-or filter, is_trashed exclusion, optional filename search,
 * optional trigger-tag join, default ordering/limit, and row → PromptAsset
 * mapping.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

type Call = { method: string; args: unknown[] };

const calls: Call[] = [];
let queryResult: { data: unknown[] | null; error: unknown };

// Every chain method appends its call onto the shared `calls` array and
// returns the same thenable builder, so we can assert both which methods
// were invoked and the order/args they were invoked with. `await q` in the
// implementation resolves via `.then`.
function makeBuilder() {
  const builder: Record<string, unknown> = {};
  ['select', 'or', 'eq', 'ilike', 'order', 'limit'].forEach((method) => {
    builder[method] = (...args: unknown[]) => {
      calls.push({ method, args });
      return builder;
    };
  });
  builder.then = (resolve: (v: unknown) => void) => resolve(queryResult);
  return builder;
}

vi.mock('../supabaseClient', () => ({
  supabase: {
    from: (...args: unknown[]) => {
      calls.push({ method: 'from', args });
      return makeBuilder();
    },
  },
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { fetchPromptAssets } from './resourceService';

beforeEach(() => {
  calls.length = 0;
  queryResult = { data: [], error: null };
});

describe('fetchPromptAssets', () => {
  it('queries resources with all four prompt columns in the or filter, over-fetches, excludes trashed, default order', async () => {
    await fetchPromptAssets();

    expect(calls[0]).toEqual({ method: 'from', args: ['resources'] });

    const orCall = calls.find((c) => c.method === 'or');
    expect(orCall?.args).toEqual(['gen_prompt.not.is.null,gen_prompt_zh.not.is.null,gen_prompt_negative.not.is.null,gen_prompt_negative_zh.not.is.null']);

    const eqCalls = calls.filter((c) => c.method === 'eq');
    expect(eqCalls).toContainEqual({ method: 'eq', args: ['is_trashed', false] });

    const orderCall = calls.find((c) => c.method === 'order');
    expect(orderCall?.args).toEqual(['updated_at', { ascending: false }]);

    const limitCall = calls.find((c) => c.method === 'limit');
    expect(limitCall?.args).toEqual([80]); // Default 50 + 30 over-fetch

    // No tag join / filter and no filename search when neither opt is given.
    const selectCall = calls.find((c) => c.method === 'select');
    expect(selectCall?.args[0]).not.toContain('resource_tags');
    expect(calls.some((c) => c.method === 'ilike')).toBe(false);
    expect(eqCalls.some((c) => c.args[0] === 'resource_tags.tag_id')).toBe(false);
  });

  it('applies ilike search on filename when query is given', async () => {
    await fetchPromptAssets({ query: 'cyber' });

    const ilikeCall = calls.find((c) => c.method === 'ilike');
    expect(ilikeCall?.args).toEqual(['filename', '%cyber%']);
  });

  it('joins resource_tags and filters by tag_id when tagId is given', async () => {
    await fetchPromptAssets({ tagId: 'tag-1' });

    const selectCall = calls.find((c) => c.method === 'select');
    expect(selectCall?.args[0]).toContain('resource_tags!inner(tag_id)');

    const eqCalls = calls.filter((c) => c.method === 'eq');
    expect(eqCalls).toContainEqual({ method: 'eq', args: ['resource_tags.tag_id', 'tag-1'] });
  });

  it('respects a custom limit and over-fetches by 30', async () => {
    await fetchPromptAssets({ limit: 10 });

    const limitCall = calls.find((c) => c.method === 'limit');
    expect(limitCall?.args).toEqual([40]); // 10 + 30 over-fetch
  });

  it('maps rows into the PromptAsset shape, stringifying id and defaulting missing fields to null', async () => {
    queryResult = {
      data: [
        {
          // Snowflake ids arrive already-stringified by bigIntSafeFetch at the
          // transport layer (see supabaseClient.ts) — a raw 17-digit JS
          // number literal would itself lose precision before reaching the
          // mapper, which is not what's under test here.
          id: '90071992547409921',
          filename: 'cyber-girl.png',
          gen_prompt: 'a cyberpunk girl',
          gen_prompt_zh: null,
          // gen_prompt_negative / gen_prompt_negative_zh intentionally absent
          updated_at: '2026-07-20T00:00:00Z',
        },
      ],
      error: null,
    };

    const result = await fetchPromptAssets();

    expect(result).toEqual([
      {
        id: '90071992547409921',
        filename: 'cyber-girl.png',
        gen_prompt: 'a cyberpunk girl',
        gen_prompt_zh: null,
        gen_prompt_negative: null,
        gen_prompt_negative_zh: null,
        updated_at: '2026-07-20T00:00:00Z',
      },
    ]);
  });

  it('filters out rows with all empty/null prompt fields, keeps rows with any non-empty prompt', async () => {
    queryResult = {
      data: [
        {
          id: '1',
          filename: 'empty.png',
          gen_prompt: null,
          gen_prompt_zh: null,
          gen_prompt_negative: '',
          gen_prompt_negative_zh: '   ', // whitespace-only, should be ignored
          updated_at: '2026-07-20T00:00:00Z',
        },
        {
          id: '2',
          filename: 'negative-only.png',
          gen_prompt: null,
          gen_prompt_zh: '',
          gen_prompt_negative: 'bad hands',
          gen_prompt_negative_zh: null,
          updated_at: '2026-07-20T00:00:00Z',
        },
      ],
      error: null,
    };

    const result = await fetchPromptAssets();

    // Only the second row should survive (has non-empty gen_prompt_negative)
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('2');
    expect(result[0].filename).toBe('negative-only.png');
  });

  it('logs and returns an empty array on query error', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    queryResult = { data: null, error: { message: 'boom' } };

    const result = await fetchPromptAssets();

    expect(result).toEqual([]);
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
