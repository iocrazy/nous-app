/**
 * My Downloads → right-click → Download Audio must send the Bearer header.
 *
 * `/api/v1/media/download/{platform_id}/music` is an `AuthDep` route (and
 * checks the caller holds the media). This handler fetched it with plain
 * `downloadFile`, which sends no credential, so the menu item was always a
 * 401 (ticket carried over from P5). Rendered the same way as the Send to Agent wiring test:
 * real DownloadsView, context menu stubbed to capture its props.
 */
import { render, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── the two collaborators under observation ────────────────────────
const { sendResourceToAgentMock, addToastMock } = vi.hoisted(() => ({
  sendResourceToAgentMock: vi.fn(),
  addToastMock: vi.fn(),
}));
vi.mock('../utils/sendResourceToAgent', () => ({
  sendResourceToAgent: sendResourceToAgentMock,
}));
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: addToastMock }) }));

// ── the context menu: capture props instead of rendering ───────────
const { menuProps } = vi.hoisted(() => ({ menuProps: { current: null as any } }));
vi.mock('./DownloadsView/DownloadContextMenu', () => ({
  DownloadContextMenu: (props: any) => {
    menuProps.current = props;
    return null;
  },
}));

// ── the media -> resource map this view feeds the handler from ─────
// Real shape: keyed by parsed_media id, values carry `resources.id`.
const { resourceDataMap } = vi.hoisted(() => ({
  resourceDataMap: {
    current: {
      '900001': {
        id: '339710259795355',
        filename: 'clip.mp4',
        mime_type: 'video/mp4',
        notes: null,
        rating: 0,
        transcript_status: 'none',
        summary_status: 'none',
      },
    } as Record<string, unknown>,
  },
}));
vi.mock('./DownloadsView/useDownloadsData', () => ({
  useResourceDataMap: () => ({
    resourceDataMap: resourceDataMap.current,
    setResourceDataMap: vi.fn(),
    resourceIdMap: {},
    aiStatusMap: {},
  }),
  useTagSearchMap: () => ({}),
  useAllTags: () => ({ allTags: [], setAllTags: vi.fn() }),
  useSelectedVideoTags: () => ({
    selectedVideoTags: [], setSelectedVideoTags: vi.fn(),
    handleAddTag: vi.fn(), handleRemoveTag: vi.fn(), refetchTags: vi.fn(),
  }),
}));

// ── everything else: mocked at the boundary, renders nothing ───────
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

const VIDEO = {
  id: '900001',
  platform_id: 'abc123',
  title: 'Some Clip',
  music_download_path: 'm/abc123.m4a',
};

vi.mock('../contexts/LibraryContext', () => ({
  useLibraryContext: () => ({
    library: [VIDEO], isLoadingLibrary: false, libraryError: null, totalCount: 1,
    hasMoreData: false, isLoadingMore: false, loadMoreRef: { current: null },
    isSentinelVisible: false, loadMoreLibrary: vi.fn(), libraryViewMode: 'grid',
    setLibraryViewMode: vi.fn(), sharedVideoIds: [], setLibrary: vi.fn(),
    loadLibraryData: vi.fn(), handleUpdateLibraryItem: vi.fn(),
    setFilterParams: vi.fn(),
  }),
}));
vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => ({ selectedTeamId: null }),
}));
vi.mock('../contexts/IslandWorkContext', () => ({
  useIslandWork: () => ({
    infoIslandEl: null, infoVisible: false,
    setInfoVisible: vi.fn(), setInfoAvailable: vi.fn(),
  }),
}));
vi.mock('../contexts/ExportTaskContext', () => ({
  useExportTasks: () => ({ tasks: [], startTask: vi.fn() }),
}));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));
vi.mock('../hooks/useFilterBarConfig', () => ({
  useFilterBarConfig: () => ({ toFilterParams: () => ({}) }),
}));
vi.mock('../hooks/useFilterBarVisibility', () => ({
  useFilterBarVisibility: () => ({ visible: false, toggle: vi.fn() }),
}));

// The card is NOT stubbed to null: it is what carries `onContextMenu`, and
// opening the menu for real is the only way the handler under test ever sees
// a `contextMenu.video`. Everything else renders nothing.
vi.mock('./CompactMediaCard', () => ({
  CompactMediaCard: (props: any) => (
    <button
      data-testid="card"
      onClick={() => props.onContextMenu({ clientX: 10, clientY: 10 }, props.data)}
    />
  ),
}));
vi.mock('./resources/filter/FilterBar', () => ({ FilterBar: () => null }));
vi.mock('./filters/FilterChipBar', () => ({ FilterChipBar: () => null }));
vi.mock('./filters/FacetPickerSheet', () => ({ FacetPickerSheet: () => null }));
vi.mock('./DownloadsView/DownloadsBatchToolbar', () => ({ DownloadsBatchToolbar: () => null }));
vi.mock('./DownloadsView/BatchTagSheet', () => ({ BatchTagSheet: () => null }));
vi.mock('./common/Loading', () => ({ default: () => null }));
vi.mock('./LibraryTable', () => ({ LibraryTable: () => null }));
vi.mock('./LibraryFeed', () => ({ LibraryFeed: () => null }));
vi.mock('./ToolbarSearch', () => ({ ToolbarSearch: () => null }));
vi.mock('./ShareModal', () => ({ ShareModal: () => null }));
vi.mock('./DownloadsView/DownloadInfoPanel', () => ({ DownloadInfoPanel: () => null }));
vi.mock('./DownloadsView/BackToTopButton', () => ({ BackToTopButton: () => null }));
vi.mock('./SearchScopePicker', () => ({
  SearchScopePicker: () => null,
  // The real loader can never return an empty scope (sanitize rejects it), and
  // an empty scope now means "the local pass cannot judge this", so the stub
  // has to carry the shipped default or these suites stop exercising it.
  loadSearchScope: () => [
    'title',
    'description',
    'author',
    'hashtags',
    'tags',
    'notes',
  ],
  saveSearchScope: () => {},
}));
vi.mock('./detail/DetailCardKit', () => ({
  loadPanelWidth: () => 360, savePanelWidth: vi.fn(),
}));
vi.mock('./DownloadsView/listStateCache', () => ({
  applyScrollOffsets: vi.fn(), clearDownloadsListState: vi.fn(),
  readDownloadsListState: () => null, readScrollOffsets: () => ({ scrollTop: 0, windowScrollY: 0 }),
  saveDownloadsListState: vi.fn(),
}));
vi.mock('../services/searchService', () => ({
  semanticSearch: vi.fn(), hybridSearch: vi.fn(), localSearch: vi.fn(), textSearch: vi.fn(),
}));
vi.mock('../services/resourceService', () => ({
  trashResourceByPlatformId: vi.fn(), updateResource: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({
  createTag: vi.fn(), addResourceTag: vi.fn(),
}));
// Partial mock: DownloadsView reaches ``mediaTypesToWire`` through
// services/searchChipFilters, and that mapping must stay REAL — it is the one
// the list path uses and the search path must not diverge from.
vi.mock('../services/dataService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/dataService')>()),
  getDownloadUrl: () => '',
  getMusicDownloadUrl: (pid: string) => `https://api.test/api/v1/media/download/${pid}/music`,
}));
const { downloadFileMock, downloadWithAuthMock } = vi.hoisted(() => ({
  downloadFileMock: vi.fn(),
  downloadWithAuthMock: vi.fn(),
}));
vi.mock('../utils/download', () => ({
  downloadFile: downloadFileMock,
  downloadWithAuth: downloadWithAuthMock,
}));
// Only the two URL builders need stubbing (they would hit the network shape);
// the rest of the module is real. Spreading the actual exports keeps the mock
// from silently omitting helpers the view picks up later — the adaptive layout
// now reads the media_type predicates from here, and a wholesale replacement
// broke this file the moment it did.
vi.mock('../utils/awemeType', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/awemeType')>()),
  getCoverUrl: () => '',
  getVideoUrl: () => '',
}));

import { DownloadsView } from './DownloadsView';

beforeEach(() => {
  resourceDataMap.current = {};
  addToastMock.mockReset();
  downloadFileMock.mockReset().mockResolvedValue(true);
  downloadWithAuthMock.mockReset().mockResolvedValue(true);
  menuProps.current = null;
});

describe('DownloadsView — Download Audio', () => {
  it('fetches the music URL with the auth headers', async () => {
    const { getAllByTestId } = render(<DownloadsView />);
    fireEvent.click(getAllByTestId('card')[0]);
    await waitFor(() => expect(menuProps.current?.contextMenu).toBeTruthy());
    const onDownloadAudio = menuProps.current.onDownloadAudio as () => Promise<void>;

    await act(async () => { await onDownloadAudio(); });

    expect(downloadWithAuthMock).toHaveBeenCalledTimes(1);
    expect(downloadWithAuthMock.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/media/download/abc123/music',
    );
    expect(downloadFileMock).not.toHaveBeenCalled();
  });
});
