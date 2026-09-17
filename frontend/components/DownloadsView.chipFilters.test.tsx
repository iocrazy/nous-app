/**
 * My Downloads — the filter chips must reach the search endpoints.
 *
 * They did not. The list pushed every chip into `fetchLibraryPaginated`
 * (applied server-side); the search box called `/search/text` with no chips
 * at all. `filteredLibrary` swaps the whole list for the hits once a backend
 * search returns, so activating a search dropped the entire toolbar —
 * silently, with the chips still rendered as active.
 *
 * Measured on production before the fix, with "AI · transcribed" ticked:
 *
 *   query   matches   transcribed   shown
 *   5000          4             1       4   <- filter dropped
 *   抖音         99             3      99   <- filter dropped
 *
 * `toSearchChipFilters` is tested as a pure unit next to itself. What is
 * asserted HERE is the wiring nothing else covers: the three call sites, and
 * that a chip change re-runs a search that is already on screen.
 *
 * Harness mirrors DownloadsView.searchScope.test.tsx.
 */
import { act, fireEvent, render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── the collaborators under observation ────────────────────────────
const {
  saveScopeMock,
  textSearchMock,
  hybridSearchMock,
  semanticSearchMock,
  toolbar,
  chips,
} = vi.hoisted(() => ({
  saveScopeMock: vi.fn(),
  textSearchMock: vi.fn(),
  hybridSearchMock: vi.fn(),
  semanticSearchMock: vi.fn(),
  toolbar: { props: null as any },
  chips: { value: {} as Record<string, unknown> },
}));

// The search box: capture its props and expose them as buttons, so the test
// can drive the same callbacks a typing user drives.
vi.mock('./ToolbarSearch', () => ({
  ToolbarSearch: (props: any) => {
    toolbar.props = props;
    return (
      <div>
        <button
          data-testid="type-query"
          onClick={() => props.onQueryChange('portrait')}
        />
        <button
          data-testid="pick-tags-scope"
          onClick={() => props.onSearchScopeChange(['tags'])}
        />
        <button
          data-testid="smart-search"
          onClick={() => props.onAISearch('portrait', 'hybrid')}
        />
        <button
          data-testid="ai-search"
          onClick={() => props.onAISearch('portrait', 'semantic')}
        />
      </div>
    );
  },
}));

vi.mock('./SearchScopePicker', () => ({
  SearchScopePicker: () => null,
  // A real, deliberately narrow stored scope: only Title is ticked.
  loadSearchScope: () => ['title'],
  saveSearchScope: saveScopeMock,
}));

vi.mock('../services/searchService', () => ({
  semanticSearch: semanticSearchMock,
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
  useTagSearchMap: () => ({ '900003': 'portrait-ish tutorial' }),
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
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

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
// The chip toolbar, driven by the test. ``toFilterParams`` returns a fresh
// object each call, exactly as the real hook does — which is why the view
// keys its memo on the serialised value rather than the object.
vi.mock('../hooks/useFilterBarConfig', () => ({
  useFilterBarConfig: () => ({ toFilterParams: () => ({ ...chips.value }) }),
}));
vi.mock('../hooks/useFilterBarVisibility', () => ({
  useFilterBarVisibility: () => ({ visible: false, toggle: vi.fn() }),
}));
vi.mock('./CompactMediaCard', () => ({
  CompactMediaCard: (props: any) => (
    <button data-testid="card" data-title={props.data.title} />
  ),
}));
vi.mock('./resources/filter/FilterBar', () => ({ FilterBar: () => null }));
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
// ``mediaTypesToWire`` stays REAL: it is the shared mapping the list path uses
// and the thing chip translation must not diverge from. Stubbing it here would
// let the two drift and the suite would still pass.
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

/** Real wire shape for /api/v1/search/text — `videos` alongside `results`. */
const EMPTY_TEXT_RESPONSE = {
  results: [],
  videos: [],
  total: 0,
  query: 'portrait',
  search_type: 'text',
};

describe('DownloadsView — filter chips reach the search', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    chips.value = {};
    saveScopeMock.mockClear();
    textSearchMock.mockReset().mockResolvedValue(EMPTY_TEXT_RESPONSE);
    hybridSearchMock.mockReset().mockResolvedValue(EMPTY_TEXT_RESPONSE);
    semanticSearchMock.mockReset().mockResolvedValue(EMPTY_TEXT_RESPONSE);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const type = async (el: HTMLElement, getByTestId: any) => {
    act(() => {
      fireEvent.click(getByTestId('type-query'));
    });
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
  };

  it('sends the active chip with the debounced quick-search', async () => {
    // The reported defect, at its own call site.
    chips.value = { ai_transcribed: true };
    const { container, getByTestId } = render(<DownloadsView />);

    await type(container, getByTestId);

    expect(textSearchMock).toHaveBeenCalled();
    expect(textSearchMock.mock.calls[0][3]).toEqual({ ai_transcribed: true });
  });

  it('sends nothing extra when the toolbar is untouched', async () => {
    // A user with no chips must issue the same request as before this change.
    const { container, getByTestId } = render(<DownloadsView />);

    await type(container, getByTestId);

    expect(textSearchMock.mock.calls[0][3]).toBeUndefined();
  });

  it('re-runs the search when a chip changes while hits are on screen', async () => {
    // While a search is active the grid IS the hits, so the list's own refetch
    // changes nothing the user can see. Without this the toolbar would move
    // and the results would not.
    chips.value = {};
    const { container, getByTestId, rerender } = render(<DownloadsView />);
    await type(container, getByTestId);
    expect(textSearchMock).toHaveBeenCalledTimes(1);

    chips.value = { ai_transcribed: true };
    rerender(<DownloadsView />);
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(textSearchMock).toHaveBeenCalledTimes(2);
    expect(textSearchMock.mock.calls[1][3]).toEqual({ ai_transcribed: true });
  });

  it('sends the chips with Smart Search too', async () => {
    // Hybrid delegates to the same RPC on the backend, so leaving it out would
    // have kept half the bug alive.
    chips.value = { platforms: ['douyin'] };
    const { getByTestId } = render(<DownloadsView />);

    await act(async () => {
      fireEvent.click(getByTestId('smart-search'));
    });

    expect(hybridSearchMock).toHaveBeenCalled();
    expect(hybridSearchMock.mock.calls[0][5]).toEqual({ platforms: ['douyin'] });
  });

  it('says so when AI Search cannot apply the chips', async () => {
    // Semantic ranks by embedding and never reaches the chips' SQL. Returning
    // a list that quietly ignores the toolbar is the exact failure this change
    // removes; reproducing it in a corner would only hide it better.
    chips.value = { ai_transcribed: true };
    const { getByText, getByTestId } = render(<DownloadsView />);

    await act(async () => {
      fireEvent.click(getByTestId('ai-search'));
    });

    expect(
      getByText(/AI Search ranks by meaning and does not apply your filters/),
    ).toBeTruthy();
  });

  it('stays quiet about AI Search when there is no chip to ignore', async () => {
    chips.value = {};
    const { queryByText, getByTestId } = render(<DownloadsView />);

    await act(async () => {
      fireEvent.click(getByTestId('ai-search'));
    });

    expect(
      queryByText(/AI Search ranks by meaning and does not apply your filters/),
    ).toBeNull();
  });
});
