import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { useResourceDataMap, useSelectedVideoTags } from './useDownloadsData';
import type { Tag } from '../../types';

// Supabase double for useResourceDataMap. The hook fires two queries per
// chunk: the resource row read, and a media_id-only prompt-presence probe
// distinguished by its `.or()` filter. The fake tells them apart the same way.
const sb = vi.hoisted(() => ({
  selects: [] as string[],
  filters: [] as string[],
  rows: [] as Record<string, unknown>[],
  promptRows: [] as Record<string, unknown>[],
}));

vi.mock('../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    from: () => {
      let isPromptProbe = false;
      const builder: Record<string, unknown> = {
        select(cols: string) {
          sb.selects.push(cols);
          return builder;
        },
        in: () => builder,
        or(filter: string) {
          sb.filters.push(filter);
          isPromptProbe = true;
          return builder;
        },
        // The query builder is a thenable; Promise.all awaits it after the
        // whole chain is built, so `isPromptProbe` is already settled here.
        then: (resolve: (v: unknown) => unknown) =>
          Promise.resolve({
            data: isPromptProbe ? sb.promptRows : sb.rows,
            error: null,
          }).then(resolve),
      };
      return builder;
    },
  }),
}));

const tagA: Tag = {
  id: 't1', name: 'Anime', color: '#fff', icon: null, type: 'user',
  created_at: '2026-01-01T00:00:00Z',
};
const tagB: Tag = {
  id: 't2', name: 'AI', color: '#6366f1', icon: null, type: 'user',
  created_at: '2026-01-01T00:00:00Z',
};

const fetchResourceTags = vi.fn();
vi.mock('../../services/resourceService', () => ({
  fetchResourceTags: (...a: unknown[]) => fetchResourceTags(...a),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));

describe('useSelectedVideoTags — R1 allTags sync', () => {
  beforeEach(() => {
    fetchResourceTags.mockReset();
  });

  it('refetchTags appends a newly-assigned tag missing from allTags', async () => {
    fetchResourceTags.mockResolvedValue([{ tag: tagA }]); // initial mount load
    const { result } = renderHook(() => useSelectedVideoTags('r1'));
    await waitFor(() => expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    let allTags: Tag[] = [tagA];
    const setAllTags = vi.fn((updater: (prev: Tag[]) => Tag[]) => {
      allTags = updater(allTags);
    });
    const { result: result2 } = renderHook(() => useSelectedVideoTags('r1', setAllTags));
    await waitFor(() => expect(result2.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    fetchResourceTags.mockResolvedValue([{ tag: tagA }, { tag: tagB }]);
    await act(async () => {
      await result2.current.refetchTags();
    });

    expect(allTags.map((t) => t.id)).toEqual(['t1', 't2']);
  });

  it('is a no-op when setAllTags is omitted (backward compatible)', async () => {
    fetchResourceTags.mockResolvedValue([{ tag: tagA }]);
    const { result } = renderHook(() => useSelectedVideoTags('r1'));
    await waitFor(() => expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    fetchResourceTags.mockResolvedValue([{ tag: tagA }, { tag: tagB }]);
    await act(async () => {
      await result.current.refetchTags();
    });

    expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }, { tag: tagB }]);
  });
});

describe('useResourceDataMap — has_prompt', () => {
  beforeEach(() => {
    sb.selects.length = 0;
    sb.filters.length = 0;
    sb.rows = [
      { id: 'r1', media_id: 'm1', notes: null, rating: 0 },
      { id: 'r2', media_id: 'm2', notes: null, rating: 0 },
    ];
    sb.promptRows = [];
  });

  it('flags only the media the prompt probe returned', async () => {
    sb.promptRows = [{ media_id: 'm2' }];
    const { result } = renderHook(() => useResourceDataMap(['m1', 'm2']));

    await waitFor(() => expect(Object.keys(result.current.resourceDataMap)).toHaveLength(2));
    expect(result.current.resourceDataMap.m1.has_prompt).toBe(false);
    expect(result.current.resourceDataMap.m2.has_prompt).toBe(true);
  });

  it('surfaces has_prompt through aiStatusMap for the card', async () => {
    sb.promptRows = [{ media_id: 'm2' }];
    const { result } = renderHook(() => useResourceDataMap(['m1', 'm2']));

    await waitFor(() => expect(result.current.aiStatusMap.m2).toBeDefined());
    expect(result.current.aiStatusMap.m2.has_prompt).toBe(true);
    // m1 has no AI state at all, so it stays out of the map entirely —
    // CompactMediaCard falls back to the media row for it.
    expect(result.current.aiStatusMap.m1).toBeUndefined();
  });

  it('probes prompt presence without pulling the prompt text', async () => {
    // gen_prompt / gen_prompt_zh are capped at 20k chars EACH; selecting them
    // for a page of rows would let prompts dominate the payload.
    renderHook(() => useResourceDataMap(['m1']));

    await waitFor(() => expect(sb.filters.length).toBeGreaterThan(0));
    expect(sb.selects).toContain('media_id');
    for (const cols of sb.selects) {
      expect(cols).not.toContain('gen_prompt');
      expect(cols).not.toContain('slide_prompts');
    }
  });

  it('treats a cleared prompt as absent, not present', async () => {
    // PromptSection.commit sends value.trim(), so clearing a prompt stores ''
    // rather than NULL. `like._*` is LIKE '_%' — one char or more — which
    // excludes both; a bare not.is.null would have lit the icon on ''.
    renderHook(() => useResourceDataMap(['m1']));

    await waitFor(() => expect(sb.filters.length).toBeGreaterThan(0));
    expect(sb.filters[0]).toBe(
      'gen_prompt.like._*,gen_prompt_zh.like._*,slide_prompts.neq.{}',
    );
    expect(sb.filters[0]).not.toContain('not.is.null');
  });

  it('ignores negative prompts (nothing to show for a negative-only asset)', async () => {
    renderHook(() => useResourceDataMap(['m1']));

    await waitFor(() => expect(sb.filters.length).toBeGreaterThan(0));
    expect(sb.filters[0]).not.toContain('gen_prompt_negative');
  });
});
