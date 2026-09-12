/**
 * My Downloads → right-click → Send to Agent: the WIRING.
 *
 * Review finding I1: the two tests around this feature both stopped at the
 * ends of the chain — `DownloadContextMenu.test.tsx` checks the menu calls
 * the prop it was handed, `downloadAgentPayload.test.ts` checks a pure
 * function's return value — and the segment between them, the part living in
 * DownloadsView, was covered by nothing. Two mutations proved it: replacing
 * `onSendToAgent={handleCtxSendToAgent}` with `() => {}` (the feature is dead
 * again, which is the exact bug this branch fixes) and deleting the failure
 * toast (a silent no-op on a user-action -> agent path) both left all 5078
 * tests green.
 *
 * So this renders the real DownloadsView, with the context menu stubbed to
 * capture the props it is actually handed, and invokes that captured
 * callback. Everything else is mocked at the module boundary: the assertions
 * are only about this one handler, and a heavy real render would fail for a
 * dozen unrelated reasons.
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
  music_download_path: null,
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
vi.mock('../services/dataService', () => ({
  getDownloadUrl: () => '', getMusicDownloadUrl: () => '',
}));
vi.mock('../utils/download', () => ({ downloadFile: vi.fn(), downloadWithAuth: vi.fn() }));
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

/** Open the context menu on a row the way the view itself does, then hand
 *  back the Send to Agent callback the menu was actually given. */
async function captureSendToAgent() {
  const { getAllByTestId } = render(<DownloadsView />);
  // Right-click a real card so the view sets its own `contextMenu` state —
  // the handler reads `contextMenu.video`, so a synthesised prop call would
  // test nothing.
  fireEvent.click(getAllByTestId('card')[0]);
  await waitFor(() => expect(menuProps.current?.contextMenu).toBeTruthy());
  return menuProps.current.onSendToAgent as () => Promise<void>;
}

const LINKED_MAP = {
  '900001': {
    id: '339710259795355',
    filename: 'clip.mp4',
    mime_type: 'video/mp4',
    notes: null,
    rating: 0,
    transcript_status: 'none',
    summary_status: 'none',
  },
};

beforeEach(() => {
  resourceDataMap.current = { ...LINKED_MAP };
  sendResourceToAgentMock.mockReset().mockResolvedValue(undefined);
  addToastMock.mockReset();
  menuProps.current = null;
});

describe('DownloadsView — Send to Agent wiring', () => {
  it('sends the resource to the agent, keyed by resources.id', async () => {
    const onSendToAgent = await captureSendToAgent();
    await act(async () => { await onSendToAgent(); });

    expect(sendResourceToAgentMock).toHaveBeenCalledTimes(1);
    const [resource, opts] = sendResourceToAgentMock.mock.calls[0];
    // The row on screen is parsed_media 900001; what an agent can be handed
    // is resources 339710259795355. Passing the former addresses a different
    // row entirely at the transcribe endpoint.
    expect(resource.id).toBe('339710259795355');
    expect(resource.mime_type).toBe('video/mp4');
    // Dropping these re-bills a transcription on an already-processed video.
    expect(resource.transcript_status).toBe('none');
    expect(opts.scope).toEqual({ type: 'personal', id: '' });
  });

  it('tells the user when no library resource is linked', async () => {
    // The page's media -> resource map has not landed (or this media has no
    // resource row). There is nothing an agent could be given, and saying
    // nothing is the silent no-op this codebase keeps paying for.
    resourceDataMap.current = {};
    const onSendToAgent = await captureSendToAgent();
    await act(async () => { await onSendToAgent(); });

    expect(sendResourceToAgentMock).not.toHaveBeenCalled();
    expect(addToastMock).toHaveBeenCalledTimes(1);
    expect(addToastMock.mock.calls[0][1]).toBe('error');
  });
});
