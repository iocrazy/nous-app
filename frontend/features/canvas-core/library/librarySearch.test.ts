// features/canvas-core/library/librarySearch.test.ts
//
// The three-store index. Fixtures are REAL wire shapes: every Snowflake is a
// JSON string, because that is what all three routers emit (the resources
// search router builds its row by hand with `"id": str(row["id"])`).

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const searchAssets = vi.fn();
const listAssets = vi.fn();
const searchResources = vi.fn();
const fetchGenerated = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
  listAssets: (...a: unknown[]) => listAssets(...a),
}));
vi.mock('../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

import {
  assetToLibraryItem,
  generatedToLibraryItem,
  startOfTodayIso,
  uploadToLibraryItem,
  useLibrarySearch,
} from './librarySearch';

const SCOPE = '727145299382534100';
const CANVAS = '900000000000000001';

/** `GET /api/v1/assets` row (AssetSummary). */
const ASSET_ROW = {
  id: '727145299382534300',
  scope_id: SCOPE,
  asset_type: 'character' as const,
  name: 'Cole Bannon',
  role_tag: 'lead',
  readiness: { state: 'draft' as const, missing: ['portrait'] },
  cover_file_id: '600000000000000001',
  is_system_preset: false,
};

/** `GET /api/v1/resources/search` row — id is a STRING (`str(row["id"])`). */
const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 812345,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};

/** `GET /api/v1/generated` item, as `GeneratedItem` documents it. */
const GENERATED_ROW = {
  id: '800000000000000001',
  scope_id: SCOPE,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'A wide shot of the harbour.',
  model: 'doubao-seedream',
  provider: 'volcengine',
  origin_kind: 'canvas_run',
  canvas_id: CANVAS,
  node_id: 'node-7',
  created_at: '2026-09-02T12:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed' as const,
  source_asset_id: null,
  source: {
    kind: 'canvas_run',
    label: 'Canvas · Harbour',
    canvas_id: CANVAS,
    node_id: 'node-7',
    shot_id: null,
    conversation_id: null,
    deep_link: `/projects/1/canvas/${CANVAS}`,
  },
  title: 'A wide shot of the harbour',
};

const OPTS = { scopeId: SCOPE, canvasId: CANVAS, generatedScope: 'this-canvas' as const };

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  searchAssets.mockReset().mockResolvedValue([ASSET_ROW]);
  listAssets.mockReset().mockResolvedValue([ASSET_ROW]);
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  fetchGenerated.mockReset().mockResolvedValue({
    items: [GENERATED_ROW],
    next_cursor: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('normalisers', () => {
  it('an asset keeps its string id and reports draft readiness', () => {
    expect(assetToLibraryItem(ASSET_ROW)).toEqual({
      store: 'assets',
      id: '727145299382534300',
      title: 'Cole Bannon',
      thumbUrl: expect.stringContaining('/api/v1/resources/600000000000000001/cover'),
      kind: 'character',
      ready: false,
    });
  });

  it('an upload with no cover gets an empty thumbUrl, never a broken one', () => {
    expect(uploadToLibraryItem({ ...UPLOAD_ROW, thumbnail_url: null }).thumbUrl).toBe('');
  });

  it('a generation points at its cover endpoint and carries its media kind', () => {
    const item = generatedToLibraryItem(GENERATED_ROW);
    expect(item.id).toBe('800000000000000001');
    expect(item.kind).toBe('image');
    expect(item.thumbUrl).toContain('/api/v1/generated-media/800000000000000001/cover');
    // Through the canvas preview tier, not the raw endpoint. Without this the
    // `toContain` above passes even if `mediaSrc` were dropped, and the
    // thumbnail would quietly serve the pre-W1 full original out of the
    // seven-day immutable cache.
    expect(item.thumbUrl).toContain('v=2');
  });
});

describe('useLibrarySearch', () => {
  it('asks all three stores in parallel and reports each one separately', async () => {
    const { result } = renderHook(() => useLibrarySearch('harbour', OPTS));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.items).toHaveLength(1);
      expect(result.current.uploads.items).toHaveLength(1);
      expect(result.current.generated.items).toHaveLength(1);
    });
    expect(searchAssets).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      q: 'harbour',
      library: 'in',
    }));
    expect(searchResources).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'harbour', teamId: SCOPE }),
    );
    expect(fetchGenerated).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      canvasId: CANVAS,
      state: 'all',
    }));
  });

  it('a store reads as pending during the debounce window, not as empty', () => {
    const { result } = renderHook(() => useLibrarySearch('harbour', OPTS));
    // Nothing has advanced the clock, so the request has not gone out yet.
    // What the shelf must NOT be told here is "loaded, and there is nothing" —
    // an empty list with `loading` false is indistinguishable from a library
    // that genuinely holds nothing, and that is what the user would read.
    expect(result.current.assets.items).toEqual([]);
    expect(result.current.assets.loading).toBe(true);
    expect(result.current.uploads.loading).toBe(true);
    expect(result.current.generated.loading).toBe(true);
  });

  it('one store failing leaves the other two with their rows', async () => {
    searchAssets.mockRejectedValue(new Error('assets down'));
    const { result } = renderHook(() => useLibrarySearch('', OPTS));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.error?.message).toBe('assets down');
    });
    expect(result.current.assets.items).toEqual([]);
    expect(result.current.uploads.items).toHaveLength(1);
    expect(result.current.generated.items).toHaveLength(1);
  });

  it('an empty scopeId is a refusal, not an unscoped query', async () => {
    const { result } = renderHook(() => useLibrarySearch('', { scopeId: '' }));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.error).not.toBeNull();
    });
    expect(searchAssets).not.toHaveBeenCalled();
    expect(searchResources).not.toHaveBeenCalled();
    expect(fetchGenerated).not.toHaveBeenCalled();
  });

  it('a superseded query never lands: only the last one is kept', async () => {
    let release: (v: unknown) => void = () => {};
    searchAssets.mockImplementationOnce(
      () => new Promise((r) => { release = r; }),
    );
    const { rerender, result } = renderHook(
      ({ q }) => useLibrarySearch(q, OPTS),
      { initialProps: { q: 'a' } },
    );
    await act(async () => { vi.advanceTimersByTime(300); });
    rerender({ q: 'b' });
    await act(async () => { vi.advanceTimersByTime(300); });
    // The FIRST call resolves late with a row that must be discarded.
    await act(async () => {
      release([{ ...ASSET_ROW, id: '1', name: 'Stale' }]);
    });
    await waitFor(() => expect(result.current.assets.items).toHaveLength(1));
    expect(result.current.assets.items[0].title).toBe('Cole Bannon');
  });

  it('generated is narrowed client-side, because that endpoint has no q', async () => {
    fetchGenerated.mockResolvedValue({
      items: [GENERATED_ROW, { ...GENERATED_ROW, id: '2', title: 'A quiet forest' }],
      next_cursor: null,
    });
    const { result } = renderHook(() => useLibrarySearch('harbour', OPTS));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(result.current.generated.items).toHaveLength(1));
    expect(result.current.generated.items[0].title).toBe('A wide shot of the harbour');
  });

  it('the Today scope sends a since instant, not a client filter', async () => {
    renderHook(() => useLibrarySearch('', { scopeId: SCOPE, generatedScope: 'today' }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalled());
    expect(fetchGenerated.mock.calls[0][1].since).toBe(startOfTodayIso());
  });

  it('the This project asset scope uses listAssets, which takes a projectId', async () => {
    renderHook(() => useLibrarySearch('', {
      scopeId: SCOPE,
      assetScope: 'this-project',
      projectId: '500000000000000001',
    }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      projectId: '500000000000000001',
      library: 'in',
    }));
    expect(searchAssets).not.toHaveBeenCalled();
  });

  it('the widen toggle reaches BOTH asset branches, not just the search one', async () => {
    // Two call sites answer "assets", and only one of them is exercised by
    // the default shelf. A widen wired into the search branch alone would
    // leave "This Project" narrowed while the pill reads released.
    renderHook(() => useLibrarySearch('', { scopeId: SCOPE, assetsLibrary: 'all' }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    expect(searchAssets).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ library: 'all' }),
    );

    renderHook(() =>
      useLibrarySearch('', {
        scopeId: SCOPE,
        assetScope: 'this-project',
        projectId: '500000000000000001',
        assetsLibrary: 'all',
      }),
    );
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ library: 'all' }),
    );
  });

  it('the Files source chip reaches the wire as `sources`', async () => {
    renderHook(() =>
      useLibrarySearch('', { scopeId: SCOPE, uploadSources: 'web' }),
    );
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    expect(searchResources).toHaveBeenCalledWith(
      expect.objectContaining({ sources: 'web' }),
    );
  });

  it('no chip sends an empty `sources`, which the service then omits', async () => {
    // Empty, not absent: `searchResources` is what decides whether the query
    // string carries the parameter, and it drops an empty one. Sending
    // `sources=` instead would hand the router a value it parses to [] —
    // documented as "no filter" there, but one refactor away from an
    // `in_([])` that blanks the shelf.
    renderHook(() => useLibrarySearch('', { scopeId: SCOPE }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    expect(searchResources.mock.calls[0][0].sources).toBe('');
  });

  it('changing the source chip re-queries uploads and leaves assets alone', async () => {
    // The per-store cache key is what makes this true. Left out of
    // `uploadKey`, the chip would flip `aria-pressed` and change nothing on
    // screen — the effect would never re-run.
    const { rerender } = renderHook(
      ({ src }: { src: string }) =>
        useLibrarySearch('', { scopeId: SCOPE, uploadSources: src }),
      { initialProps: { src: '' } },
    );
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchResources).toHaveBeenCalledTimes(1));
    const assetCalls = searchAssets.mock.calls.length;

    rerender({ src: 'generated,derived' });
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchResources).toHaveBeenCalledTimes(2));
    expect(searchResources.mock.calls[1][0].sources).toBe('generated,derived');
    expect(searchAssets.mock.calls.length).toBe(assetCalls);
  });

  it('reload re-runs one store without touching the others', async () => {
    const { result } = renderHook(() => useLibrarySearch('', OPTS));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(result.current.assets.items).toHaveLength(1));
    const uploadCalls = searchResources.mock.calls.length;
    await act(async () => { result.current.assets.reload(); });
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2));
    expect(searchResources.mock.calls.length).toBe(uploadCalls);
  });
});
