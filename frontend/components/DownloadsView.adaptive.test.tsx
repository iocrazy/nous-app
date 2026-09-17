/**
 * My Downloads in the adaptive (justified) view.
 *
 * Review round 1, Important #1: the adaptive default reached the resource grid
 * but not this surface. The layout maths lives in utils/justifiedLayout.ts and
 * is tested there; what is asserted HERE is the wiring that nothing else
 * covers — that this view renders justified ROWS, sizes each card from its own
 * aspect ratio, and only attaches the measurement callback where the server
 * gave no dimensions.
 *
 * The harness mirrors DownloadsView.sendToAgent.test.tsx: everything outside
 * the branch under test is mocked at the module boundary, because a heavy real
 * render would fail for a dozen unrelated reasons.
 */
import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

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

// Real wire shape: parsed_media rows carry `resolution` as "W:H" (ytdlp writes
// it; see utils/awemeType.formatResolution) and `media_type`, NOT a mime type.
const VIDEOS = [
  { id: '900001', platform_id: 'a1', title: 'Landscape', media_type: 'video', resolution: '1920:1080', music_download_path: null },
  { id: '900002', platform_id: 'a2', title: 'Portrait', media_type: 'video', resolution: '1080:1920', music_download_path: null },
  { id: '900003', platform_id: 'a3', title: 'Square', media_type: 'video', resolution: '1000:1000', music_download_path: null },
];

vi.mock('../contexts/LibraryContext', () => ({
  useLibraryContext: () => ({
    library: VIDEOS, isLoadingLibrary: false, libraryError: null, totalCount: VIDEOS.length,
    hasMoreData: false, isLoadingMore: false, loadMoreRef: { current: null },
    isSentinelVisible: false, loadMoreLibrary: vi.fn(), libraryViewMode: 'justified',
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
      data-title={props.data.title}
      data-aspect={String(props.aspectRatio)}
      data-measurable={String(props.onThumbnailAspect !== undefined)}
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
  getMusicDownloadUrl: () => '',
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

// jsdom implements no ResizeObserver, and a real one would report 0 here
// anyway. Stub the shared width hook with a fixed container width so the row
// partition below is deterministic arithmetic.
const CONTAINER_WIDTH = 1000;
vi.mock('../hooks/useContainerWidth', () => ({
  useContainerWidth: () => ({ ref: () => {}, width: CONTAINER_WIDTH }),
}));

import { DownloadsView } from './DownloadsView';
import { computeJustifiedRows, distributeRowWidths } from '../utils/justifiedLayout';
import { parseResolution } from '../utils/resourceAspect';

const TARGET_ROW_HEIGHT = 200;
const MIN_CARD_WIDTH = 190; // mirrors JUSTIFIED_MIN_CARD_WIDTH
const GAP = 12;

describe('DownloadsView — adaptive view', () => {
  it('sizes each card by its own aspect ratio, until the stats strip needs more', () => {
    const { getAllByTestId } = render(<DownloadsView />);
    const cards = getAllByTestId('card');
    expect(cards).toHaveLength(3);

    // `data-aspect` is the card's RENDERED shape (width / row height), not the
    // media's own ratio. They agree for anything wide enough; a portrait clip
    // is where they part, because 9:16 at this row height lands under the width
    // the four-column stats strip needs and gets widened (2026-09-15 — a douyin
    // column rendered its likes/comments/shares/collects clipped).
    const byTitle = Object.fromEntries(
      cards.map((c) => [c.getAttribute('data-title'), Number(c.getAttribute('data-aspect'))]),
    );
    // The portrait clip is the one under the floor, so it widens; the others
    // fund it and therefore render slightly narrower than their own ratio.
    expect(byTitle.Portrait).toBeGreaterThan(1080 / 1920);
    expect(byTitle.Landscape).toBeLessThan(16 / 9);
    expect(byTitle.Landscape).toBeGreaterThan(byTitle.Square);
  });

  it('never renders a card too narrow for its four stat columns', () => {
    // The floor is not taste: `p-2` eats 16px and the strip's three gaps eat
    // 18px, so each of the four cells gets (W - 34) / 4, and `142.7K` at 9px
    // bold needs ~35px of it. Under ~174px the digits clip.
    const { container } = render(<DownloadsView />);
    const widths = Array.from(container.querySelectorAll('[data-testid="card"]')).map(
      (c) => parseFloat((c.parentElement as HTMLElement).style.width),
    );
    expect(widths.length).toBe(3);
    for (const w of widths) expect(w).toBeGreaterThanOrEqual(174);
  });

  it('lays the items out in justified rows at the container width', () => {
    const { container } = render(<DownloadsView />);
    const rows = container.querySelectorAll('.flex.items-start');
    expect(rows.length).toBeGreaterThan(0);

    // The rendered partition must match the shared layout function fed the same
    // inputs — that is what makes this "the same layout as My Uploads".
    const aspects = [
      parseResolution('1920:1080')!,
      parseResolution('1080:1920')!,
      parseResolution('1000:1000')!,
    ];
    const expected = computeJustifiedRows(aspects, CONTAINER_WIDTH, {
      targetRowHeight: TARGET_ROW_HEIGHT,
      gap: GAP,
    });
    expect(rows).toHaveLength(expected.length);

    // Widths come from distributeRowWidths, not from `ratio * row height`:
    // the stats-strip floor is funded by the wide items in the SAME row, so a
    // card's width is a property of its row rather than of its own ratio.
    const widths = Array.from(container.querySelectorAll('[data-testid="card"]'))
      .map((c) => (c.parentElement as HTMLElement).style.width);
    expected.forEach((row) => {
      const want = distributeRowWidths(
        aspects.slice(row.start, row.end),
        row.height,
        { minWidth: MIN_CARD_WIDTH },
      );
      for (let i = row.start; i < row.end; i += 1) {
        expect(widths[i]).toBe(`${want[i - row.start]}px`);
      }
      // Redistribution moves width WITHIN the row — the row still spans what
      // the justified pass decided, so the grid stays flush.
      const natural = aspects
        .slice(row.start, row.end)
        .reduce((a, ar) => a + ar * row.height, 0);
      expect(want.reduce((a, b) => a + b, 0)).toBeCloseTo(natural, 5);
    });
  });

  it('never attaches the measurement callback when the server sent dimensions', () => {
    // Downloads almost always carry `resolution`, so measuring would be pure
    // cost — and every report repartitions the tail.
    const { getAllByTestId } = render(<DownloadsView />);
    for (const card of getAllByTestId('card')) {
      expect(card.getAttribute('data-measurable')).toBe('false');
    }
  });
});
