/**
 * Unit tests for fetchSmartFolderResultsPaginated — pins the search_smart_folder
 * RPC param shape (keyset cursor, probe limit, count-on-first-page),
 * relative-date resolution, and keyset slicing (hasMore / nextCursor).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// Capture the args every supabase.rpc call receives, and control its result.
const rpcMock = vi.fn();
let rpcReturn: { data: unknown; error: unknown };

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
  },
}));
// Keep the heavy sibling imports inert — the module pulls these in at load.
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { fetchSmartFolderResultsPaginated } from './resourceService';

function row(id: string, created_at: string) {
  return { id, created_at, resource: { id: `r-${id}`, filename: `${id}.mp4` } };
}

beforeEach(() => {
  rpcMock.mockClear();
  rpcReturn = { data: { rows: [], total_count: null }, error: null };
});

describe('fetchSmartFolderResultsPaginated', () => {
  const rules = {
    operator: 'AND' as const,
    match: true,
    conditions: [{ field: 'file_type', op: 'eq', value: 'video' }],
  };

  it('requests count + probe limit (pageSize+1) on the first page', async () => {
    await fetchSmartFolderResultsPaginated('scope-1', rules, null, 40);
    const [name, params] = rpcMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe('search_smart_folder');
    expect(params.p_scope_id).toBe('scope-1');
    expect(params.p_limit).toBe(41);
    expect(params.p_with_count).toBe(true);
    expect(params.p_cursor_ts).toBeNull();
    expect(params.p_cursor_id).toBeNull();
  });

  it('threads the cursor and skips the count on later pages', async () => {
    await fetchSmartFolderResultsPaginated(
      'scope-1',
      rules,
      { ts: '2026-01-05T00:00:00Z', id: '90071992547409921' },
      20,
    );
    const params = rpcMock.mock.calls[0][1] as Record<string, unknown>;
    expect(params.p_limit).toBe(21);
    expect(params.p_with_count).toBe(false);
    expect(params.p_cursor_ts).toBe('2026-01-05T00:00:00Z');
    // Snowflake id passes through as a STRING — never a precision-losing number.
    expect(params.p_cursor_id).toBe('90071992547409921');
    expect(typeof params.p_cursor_id).toBe('string');
  });

  it('resolves a relative:-7d created_at to an absolute ISO timestamp', async () => {
    const relRules = {
      operator: 'AND' as const,
      match: true,
      conditions: [{ field: 'created_at', op: 'gt', value: 'relative:-7d' }],
    };
    const before = Date.now();
    await fetchSmartFolderResultsPaginated('scope-1', relRules, null, 40);
    const params = rpcMock.mock.calls[0][1] as {
      p_rules: { conditions: Array<{ value: string }> };
    };
    const resolved = params.p_rules.conditions[0].value;
    expect(resolved).not.toBe('relative:-7d');
    const t = Date.parse(resolved);
    expect(Number.isNaN(t)).toBe(false);
    // ~7 days in the past, give or take the test's wall-clock drift.
    const sevenDays = 7 * 86_400_000;
    expect(before - t).toBeGreaterThan(sevenDays - 60_000);
    expect(before - t).toBeLessThan(sevenDays + 60_000);
  });

  it('leaves non-relative condition values untouched', async () => {
    await fetchSmartFolderResultsPaginated('scope-1', rules, null, 40);
    const params = rpcMock.mock.calls[0][1] as {
      p_rules: { conditions: Array<{ value: string }> };
    };
    expect(params.p_rules.conditions[0].value).toBe('video');
  });

  it('slices pageSize+1 rows into a page + nextCursor (hasMore=true)', async () => {
    // pageSize 2, return 3 rows → hasMore, drop the probe row.
    rpcReturn = {
      data: {
        rows: [
          row('1003', '2026-01-03T00:00:00Z'),
          row('1002', '2026-01-02T00:00:00Z'),
          row('1001', '2026-01-01T00:00:00Z'),
        ],
        total_count: 9,
      },
      error: null,
    };
    const page = await fetchSmartFolderResultsPaginated('scope-1', rules, null, 2);
    expect(page.data).toHaveLength(2);
    expect(page.hasMore).toBe(true);
    expect(page.nextCursor).toEqual({ ts: '2026-01-02T00:00:00Z', id: '1002' });
    expect(page.totalCount).toBe(9);
  });

  it('reports the last page (hasMore=false, no cursor) and totalCount -1 when null', async () => {
    rpcReturn = {
      data: { rows: [row('1001', '2026-01-01T00:00:00Z')], total_count: null },
      error: null,
    };
    const page = await fetchSmartFolderResultsPaginated('scope-1', rules, null, 2);
    expect(page.data).toHaveLength(1);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
    expect(page.totalCount).toBe(-1);
  });

  it('throws when the RPC returns an error', async () => {
    rpcReturn = { data: null, error: { message: 'boom' } };
    await expect(
      fetchSmartFolderResultsPaginated('scope-1', rules, null, 40),
    ).rejects.toBeTruthy();
  });
});
