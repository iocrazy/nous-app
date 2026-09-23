/**
 * My Downloads — vector search hits (Layer / Sort / legs chips, card badges).
 *
 * Harness: DownloadsView.searchScope.test.tsx's module-boundary mocks, with
 * the FilterBar reduced to its `trailing` slot and the card reduced to the
 * `hit` it receives. The hybrid response below is the REAL wire shape of
 * /api/v1/search/hybrid after the vector-spaces backend: numeric `media_id`,
 * numeric `similarity_score`, string `platform_id`, per-row `layer`, and
 * response-level `vector_leg` / `legs` / `reranked` / `processing_time_ms`.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { hybridSearchMock, textSearchMock, navigateMock, toolbar } = vi.hoisted(() => ({
  hybridSearchMock: vi.fn(),
  textSearchMock: vi.fn(),
  navigateMock: vi.fn(),
  toolbar: { props: null as any },
}));

vi.mock('./ToolbarSearch', () => ({
  ToolbarSearch: (props: any) => {
    toolbar.props = props;
    return (
      <div>
        <button data-testid="smart-search" onClick={() => props.onAISearch('q', 'hybrid')} />
        <button data-testid="clear-search" onClick={() => props.onClear()} />
      </div>
    );
  },
}));
vi.mock('./SearchScopePicker', () => ({
  SearchScopePicker: () => null,
  loadSearchScope: () => ['title'],
  saveSearchScope: vi.fn(),
}));
vi.mock('../services/searchService', () => ({
  semanticSearch: vi.fn(),
  hybridSearch: hybridSearchMock,
  localSearch: vi.fn(() => ({ results: [] })),
  textSearch: textSearchMock,
}));

// ── everything else: mocked at the boundary, renders nothing ───────
vi.mock('../utils/sendResourceToAgent', () => ({ sendResourceToAgent: vi.fn() }));
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('./DownloadsView/DownloadContextMenu', () => ({
  DownloadContextMenu: () => null,
}));
vi.mock('./DownloadsView/useDownloadsData', () => ({
  useResourceDataMap: () => ({
    resourceDataMap: {},
    setResourceDataMap: vi.fn(),
    resourceIdMap: {},
    aiStatusMap: {},
  }),
  // Pre-joined tag names, keyed by parsed_media id — the shape the view feeds
  // into the local matcher's `tagText` argument.
  useTagSearchMap: () => ({}),
  useAllTags: () => ({ allTags: [], setAllTags: vi.fn() }),
  useSelectedVideoTags: () => ({
    selectedVideoTags: [],
    setSelectedVideoTags: vi.fn(),
    handleAddTag: vi.fn(),
    handleRemoveTag: vi.fn(),
    refetchTags: vi.fn(),
  }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown, opts?: Record<string, unknown>) => {
      const text = typeof def === 'string' ? def : _k;
      const vars = typeof def === 'object' && def ? (def as Record<string, unknown>) : opts;
      return text.replace(/{{(\w+)}}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));

// Library rows are deliberately NOT the hits: the hits render from the slim
// SearchResultItem projection, which is the path that carries created_at.
// Real wire shape: parsed_media rows carry `resolution` as "W:H" and
// `media_type`, and the author lives on `author_nickname`.
//   a1 — the word is in the TITLE       (in scope while scope = ['title'])
//   a2 — the word is in the DESCRIPTION (out of scope)
//   a3 — the word is only in the TAGS   (in scope only after ticking Tags)
const VIDEOS = [
  {
    id: '900001',
    platform_id: 'a1',
    title: 'Portrait lighting',
    description: 'nothing here',
    media_type: 'video',
    resolution: '1080:1920',
    music_download_path: null,
  },
  {
    id: '900002',
    platform_id: 'a2',
    title: 'Landscape lighting',
    description: 'a portrait rig, described only',
    media_type: 'video',
    resolution: '1920:1080',
    music_download_path: null,
  },
  {
    id: '900003',
    platform_id: 'a3',
    title: 'Square rig',
    description: 'nothing here',
    media_type: 'video',
    resolution: '1000:1000',
    music_download_path: null,
  },
];

vi.mock('../contexts/LibraryContext', () => ({
  useLibraryContext: () => ({
    library: VIDEOS,
    isLoadingLibrary: false,
    libraryError: null,
    totalCount: VIDEOS.length,
    hasMoreData: false,
    isLoadingMore: false,
    loadMoreRef: { current: null },
    isSentinelVisible: false,
    loadMoreLibrary: vi.fn(),
    libraryViewMode: 'justified',
    setLibraryViewMode: vi.fn(),
    sharedVideoIds: [],
    setLibrary: vi.fn(),
    loadLibraryData: vi.fn(),
    handleUpdateLibraryItem: vi.fn(),
    setFilterParams: vi.fn(),
  }),
}));
vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => ({ selectedTeamId: null }),
}));
vi.mock('../contexts/IslandWorkContext', () => ({
  useIslandWork: () => ({
    infoIslandEl: null,
    infoVisible: false,
    setInfoVisible: vi.fn(),
    setInfoAvailable: vi.fn(),
  }),
}));
vi.mock('../contexts/ExportTaskContext', () => ({
  useExportTasks: () => ({ tasks: [], startTask: vi.fn() }),
}));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: null }) }));
vi.mock('../hooks/useFilterBarConfig', () => ({
  useFilterBarConfig: () => ({ toFilterParams: () => ({}) }),
}));
vi.mock('../hooks/useFilterBarVisibility', () => ({
  useFilterBarVisibility: () => ({ visible: true, toggle: vi.fn() }),
}));
vi.mock('./CompactMediaCard', () => ({
  CompactMediaCard: (props: any) => (
    <button
      data-testid="card"
      data-title={props.data.title}
      data-hit={props.hit ? `${props.hit.layer}:${props.hit.score}` : ''}
      onDoubleClick={props.onDoubleClick}
    />
  ),
}));
// The real bar needs ResourcesContext; this one just mounts the trailing slot,
// which is the only thing the search increment hands it.
vi.mock('./resources/filter/FilterBar', () => ({
  FilterBar: (props: any) => <div data-testid="filter-bar">{props.trailing}</div>,
}));
vi.mock('./filters/FilterChipBar', () => ({ FilterChipBar: () => null }));
vi.mock('./filters/FacetPickerSheet', () => ({ FacetPickerSheet: () => null }));
vi.mock('./DownloadsView/DownloadsBatchToolbar', () => ({
  DownloadsBatchToolbar: () => null,
}));
vi.mock('./DownloadsView/BatchTagSheet', () => ({ BatchTagSheet: () => null }));
vi.mock('./common/Loading', () => ({ default: () => null }));
vi.mock('./LibraryTable', () => ({ LibraryTable: () => null }));
vi.mock('./LibraryFeed', () => ({ LibraryFeed: () => null }));
vi.mock('./ShareModal', () => ({ ShareModal: () => null }));
vi.mock('./DownloadsView/DownloadInfoPanel', () => ({ DownloadInfoPanel: () => null }));
vi.mock('./DownloadsView/BackToTopButton', () => ({ BackToTopButton: () => null }));
vi.mock('./detail/DetailCardKit', () => ({
  loadPanelWidth: () => 360,
  savePanelWidth: vi.fn(),
}));
vi.mock('./DownloadsView/listStateCache', () => ({
  applyScrollOffsets: vi.fn(),
  clearDownloadsListState: vi.fn(),
  readDownloadsListState: () => null,
  readScrollOffsets: () => ({ scrollTop: 0, windowScrollY: 0 }),
  saveDownloadsListState: vi.fn(),
}));
vi.mock('../services/resourceService', () => ({
  trashResourceByPlatformId: vi.fn(),
  updateResource: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({
  createTag: vi.fn(),
  addResourceTag: vi.fn(),
}));
// Partial mock: DownloadsView reaches ``mediaTypesToWire`` through
// services/searchChipFilters, and that mapping must stay REAL — it is the one
// the list path uses and the search path must not diverge from.
vi.mock('../services/dataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/dataService')>()),
  getDownloadUrl: () => '',
  getMusicDownloadUrl: () => '',
}));
vi.mock('../utils/download', () => ({ downloadFile: vi.fn(), downloadWithAuth: vi.fn() }));
vi.mock('../utils/awemeType', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/awemeType')>()),
  getCoverUrl: () => '',
  getVideoUrl: () => '',
}));
vi.mock('../hooks/useContainerWidth', () => ({
  useContainerWidth: () => ({ ref: () => {}, width: 1000 }),
}));

import { DownloadsView } from './DownloadsView';

const HYBRID_RESPONSE = {
  results: [
    {
      media_id: 1, platform_id: 'a', title: 'A', cover_url: null, author: null,
      similarity_score: 1.0, description: null, tags: [], view_count: 0,
      created_at: '2026-09-01T00:00:00Z', layer: 'text',
    },
    {
      media_id: 2, platform_id: 'b', title: 'B', cover_url: null, author: null,
      similarity_score: 0.71, description: null, tags: [], view_count: 0,
      created_at: '2026-09-10T00:00:00Z', layer: 'semantic',
    },
  ],
  videos: [],
  total: 2,
  query: 'q',
  search_type: 'hybrid',
  vector_leg: 'ok',
  legs: { text: 1, semantic: 1 },
  reranked: false,
  processing_time_ms: 412,
};

/** Same response from a backend that predates the vector-spaces PR. */
const LEGACY_HYBRID_RESPONSE = {
  results: HYBRID_RESPONSE.results.map(({ layer: _layer, ...rest }) => rest),
  videos: [],
  total: 2,
  query: 'q',
  search_type: 'hybrid',
  processing_time_ms: 300,
};

const cards = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('[data-testid="card"]')).map((c) => ({
    title: c.getAttribute('data-title'),
    hit: c.getAttribute('data-hit'),
  }));

async function runSmartSearch() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('smart-search'));
  });
}

describe('DownloadsView — vector search hits', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    textSearchMock.mockReset();
    hybridSearchMock.mockReset().mockResolvedValue(HYBRID_RESPONSE);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the legs chips and hands each card its hit, best match first', async () => {
    const { container } = render(<DownloadsView />);
    expect(screen.queryByTestId('search-legs-chips')).toBeNull();

    await runSmartSearch();

    expect(screen.getByTestId('search-legs-chips')).toBeInTheDocument();
    expect(screen.getByText('Text 1')).toBeInTheDocument();
    expect(screen.getByText('Semantic 1')).toBeInTheDocument();
    expect(screen.getByText(/2 hits/)).toBeInTheDocument();
    expect(cards(container)).toEqual([
      { title: 'A', hit: 'text:1' },
      { title: 'B', hit: 'semantic:0.71' },
    ]);
  });

  it('Layer = Semantic keeps only the semantic hit', async () => {
    const { container } = render(<DownloadsView />);
    await runSmartSearch();

    fireEvent.click(screen.getByRole('button', { name: /Layer · All/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Semantic' }));

    expect(cards(container).map((c) => c.title)).toEqual(['B']);
  });

  it('Sort = Date orders hits by created_at, newest first', async () => {
    const { container } = render(<DownloadsView />);
    await runSmartSearch();

    fireEvent.click(screen.getByRole('button', { name: /Sort · Similarity/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Date' }));

    expect(cards(container).map((c) => c.title)).toEqual(['B', 'A']);
  });

  it('clearing the search removes the chips, the badges and resets Layer / Sort', async () => {
    const { container } = render(<DownloadsView />);
    await runSmartSearch();
    fireEvent.click(screen.getByRole('button', { name: /Layer · All/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Semantic' }));

    await act(async () => {
      fireEvent.click(screen.getByTestId('clear-search'));
    });
    expect(screen.queryByTestId('search-legs-chips')).toBeNull();
    expect(cards(container).every((c) => c.hit === '')).toBe(true);

    // A fresh search starts from Layer = All again, not the stale Semantic.
    await runSmartSearch();
    expect(screen.getByRole('button', { name: /Layer · All/ })).toBeInTheDocument();
    expect(cards(container).map((c) => c.title)).toEqual(['A', 'B']);
  });

  it('opening a hit carries its search hit into the detail page', async () => {
    // Backend-hydrated `videos` carry resource_id — the row the detail route needs.
    hybridSearchMock.mockReset().mockResolvedValue({
      ...HYBRID_RESPONSE,
      videos: [
        { id: '1', platform_id: 'a', title: 'A', resource_id: 7001 },
        { id: '2', platform_id: 'b', title: 'B', resource_id: 7002 },
      ],
    });
    navigateMock.mockReset();
    const { container } = render(<DownloadsView />);
    await runSmartSearch();

    const cardB = Array.from(container.querySelectorAll('[data-testid="card"]')).find(
      (c) => c.getAttribute('data-title') === 'B',
    )!;
    await act(async () => {
      fireEvent.doubleClick(cardB);
    });

    expect(navigateMock).toHaveBeenCalledWith('/resources/file/7002', {
      state: expect.objectContaining({ searchHit: { layer: 'semantic', score: 0.71 } }),
    });
  });

  it('opening an item outside a search carries no search hit', async () => {
    navigateMock.mockReset();
    const row = VIDEOS[0] as Record<string, unknown>;
    row.resource_id = 7100;
    try {
      const { container } = render(<DownloadsView />);
      await act(async () => {
        fireEvent.doubleClick(container.querySelector('[data-testid="card"]')!);
      });
      expect(navigateMock).toHaveBeenCalledTimes(1);
      expect(navigateMock.mock.calls[0][1].state.searchHit).toBeUndefined();
    } finally {
      delete row.resource_id;
    }
  });

  it('degrades without a trace when the backend has no legs / layer', async () => {
    hybridSearchMock.mockReset().mockResolvedValue(LEGACY_HYBRID_RESPONSE);
    const { container } = render(<DownloadsView />);
    await runSmartSearch();

    expect(screen.queryByTestId('search-legs-chips')).toBeNull();
    expect(cards(container)).toEqual([
      { title: 'A', hit: '' },
      { title: 'B', hit: '' },
    ]);
  });
});
