/**
 * My Downloads — search-scope wiring.
 *
 * `localSearchMatch.ts` and `SearchScopePicker.loadScope` are each tested as
 * pure units. What is asserted HERE is the three call sites in DownloadsView
 * that nothing else covers, and that all three were the actual defect:
 *
 *   1. the instant local filter is fed `searchScope` (it used to match a fixed
 *      field list, so for ~300ms the list contradicted the backend answer);
 *   2. the debounced backend quick-search forwards `searchScope`;
 *   3. a scope change is persisted through the SHARED writer — this view used
 *      to inline `localStorage.setItem` against the pre-v2 key, so a choice
 *      made here was invisible to `loadSearchScope`.
 *
 * The harness mirrors DownloadsView.adaptive.test.tsx: everything outside the
 * branch under test is mocked at the module boundary, because a heavy real
 * render would fail for a dozen unrelated reasons.
 */
import { act, fireEvent, render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── the collaborators under observation ────────────────────────────
const { saveScopeMock, textSearchMock, hybridSearchMock, toolbar } = vi.hoisted(
  () => ({
    saveScopeMock: vi.fn(),
    textSearchMock: vi.fn(),
    hybridSearchMock: vi.fn(),
    toolbar: { props: null as any },
  }),
);

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
vi.mock('../hooks/useFilterBarConfig', () => ({
  useFilterBarConfig: () => ({ toFilterParams: () => ({}) }),
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
vi.mock('../services/dataService', () => ({
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

const titles = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('[data-testid="card"]')).map((c) =>
    c.getAttribute('data-title'),
  );

describe('DownloadsView — search scope wiring', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    saveScopeMock.mockClear();
    textSearchMock.mockReset().mockResolvedValue(EMPTY_TEXT_RESPONSE);
    hybridSearchMock.mockReset().mockResolvedValue(EMPTY_TEXT_RESPONSE);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('filters the loaded rows through the stored scope, not a fixed field list', () => {
    const { container, getByTestId } = render(<DownloadsView />);
    expect(titles(container)).toHaveLength(3);

    // Stored scope is ['title'] only. The debounce has NOT fired yet, so this
    // is purely the local pass.
    act(() => {
      fireEvent.click(getByTestId('type-query'));
    });

    // Matched on title — kept. Matched only on description / tags — dropped,
    // because the user unticked those. Before the fix all three survived on
    // the description hit and the list disagreed with the backend 300ms later.
    expect(titles(container)).toEqual(['Portrait lighting']);
  });

  it('re-filters against the tag text when the user ticks Tags', () => {
    const { container, getByTestId } = render(<DownloadsView />);

    act(() => {
      fireEvent.click(getByTestId('type-query'));
    });
    expect(titles(container)).toEqual(['Portrait lighting']);

    act(() => {
      fireEvent.click(getByTestId('pick-tags-scope'));
    });

    // Scope is now ['tags']: the title hit drops out and the row whose TAG
    // text carries the word appears. Proves the memo actually re-runs on a
    // scope change (searchScope is in its dependency list).
    expect(titles(container)).toEqual(['Square rig']);
  });

  it('persists a scope change through the shared writer', () => {
    const { getByTestId } = render(<DownloadsView />);

    act(() => {
      fireEvent.click(getByTestId('pick-tags-scope'));
    });

    // Not an inline setItem against the pre-v2 key — the exported writer, so
    // loadSearchScope can read the choice back.
    expect(saveScopeMock).toHaveBeenCalledWith(['tags']);
  });

  it('forwards the scope on the debounced backend quick-search', async () => {
    const { getByTestId } = render(<DownloadsView />);

    act(() => {
      fireEvent.click(getByTestId('type-query'));
    });
    expect(textSearchMock).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(350);
    });

    expect(textSearchMock).toHaveBeenCalledWith('portrait', 1000, ['title']);
  });

  it('forwards the scope on Smart Search (hybrid)', async () => {
    const { getByTestId } = render(<DownloadsView />);

    await act(async () => {
      fireEvent.click(getByTestId('smart-search'));
    });

    // Hybrid used to hardcode the four basic fields backend-side; the client
    // has to send the scope for the backend fix to be reachable at all.
    expect(hybridSearchMock).toHaveBeenCalledWith('portrait', {}, 100, 0.5, [
      'title',
    ]);
  });
});
