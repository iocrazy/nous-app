// features/canvas-core/library/LibraryPanel.test.tsx
//
// The island panel. What these cases pin, in the order the spec asks for it:
// it is absent until something opens it; it is a dialog on the canvas island;
// it shows one Media segment at a time; it names the node it is aiming at and
// lets go of it three different ways (✕, Escape, the node being deleted); its
// keystrokes do not reach the canvas shortcuts while Escape deliberately does;
// and both footer actions report every outcome instead of closing quietly.
//
// jsdom lays nothing out, so the justified pre-pass would measure a container
// width of 0 and answer with no rows — every cell assertion would then pin
// "the grid rendered nothing". Same stubs as LibraryGrid.test.tsx, plus the
// observers jsdom does not implement (absent, they warn and the run stops
// being pristine).

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue)
              .replace(/\{\{count\}\}/g, String((d as { count?: number }).count ?? ''))
              .replace(/\{\{title\}\}/g, String((d as { title?: string }).title ?? ''))
              .replace(/\{\{model\}\}/g, String((d as { model?: string }).model ?? ''))
              .replace(/\{\{used\}\}/g, String((d as { used?: number }).used ?? ''))
              .replace(/\{\{max\}\}/g, String((d as { max?: number }).max ?? ''))
          : k,
  }),
}));

const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast: (...a: unknown[]) => addToast(...a) }),
}));

const searchResources = vi.fn();
const searchAssets = vi.fn();
const listAssets = vi.fn();
const fetchGenerated = vi.fn();
const listGenerationCapabilities = vi.fn();
const placeLibraryItems = vi.fn();
const addReferences = vi.fn();
const importResourceAsCanvasMedia = vi.fn();

vi.mock('../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
  listAssets: (...a: unknown[]) => listAssets(...a),
}));
vi.mock('../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));
vi.mock('../services/canvasGenerationService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));
vi.mock('./placeLibraryItems', () => ({
  placeLibraryItems: (...a: unknown[]) => placeLibraryItems(...a),
}));
vi.mock('./addReferences', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  addReferences: (...a: unknown[]) => addReferences(...a),
}));
// The mention path does NOT go through the mocked `addReferences` — it resolves
// each picked row through the real `resolveReferenceRefs`, which mints a durable
// url for an upload. Left unmocked it would reach the network from a unit test.
vi.mock('../smart/mediaImport', () => ({
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));

import { MAX_REFERENCE_IMAGES } from '../smart/refOrder';
import { _resetModelCapabilitiesCache } from '../smart/nodes/useModelCapabilities';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { LibraryPanel } from './LibraryPanel';
import { registerMentionHandle } from './mentionHandles';
import { PREVIEW_DELAY_MS } from './LibraryPreviewCard';
import { useLibraryStore } from './libraryStore';

const SCOPE = '727145299382534100';

/** `GET /api/v1/resources/search` row — the real wire shape, string ids. */
const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};

/** A second row, so a case can select TWO and assert the plural the app ships.
 *  Same wire shape — string id, relative `thumbnail_url`. */
const SECOND_UPLOAD_ROW = {
  id: '655000000000000002',
  name: 'harbour-dawn.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:13Z',
  thumbnail_url: '/api/v1/resources/655000000000000002/cover',
  transcript_status: null,
  summary_status: null,
};

/** `GET /api/v1/assets/search` row — the real wire shape, string ids. */
const ASSET_ROW = {
  id: '727145299382534300',
  scope_id: SCOPE,
  asset_type: 'character' as const,
  name: 'Cole Bannon',
  role_tag: 'lead',
  readiness: { state: 'ready' as const, missing: [] },
  cover_file_id: '600000000000000001',
  is_system_preset: false,
};

class ObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

let realRect: () => DOMRect;

function seedNodes(nodes: unknown[]): void {
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: nodes as never,
    connections: [],
    selection: [],
  });
}

/** A prompt card with a generation model, so the quota line has real inputs. */
function promptNode(manualRefs: Array<{ url: string; kind: string }> = []) {
  return {
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 0 },
    data: {
      body: 'Harbour at dusk',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      manual_refs: manualRefs,
      gen: { kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1 },
    },
  };
}

/** A prompt card with NO `gen` — the legacy/Text kind. A text run sends
 *  `body` only (`runner.backend.ts`), so a reference added here would be
 *  dropped without a word; the panel offers MENTIONS instead. */
function textPromptNode() {
  return {
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 0 },
    data: {
      body: 'Harbour at dusk',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      gen: null,
    },
  };
}

/** Stand in for the node's body editor. The real one is registered by
 *  `PromptNodeView`; the panel only ever sees it through the registry. */
const insertImage = vi.fn();
const insertAsset = vi.fn();
const handleUndos: Array<() => void> = [];
function armMentionHandle(nodeId = 'p1') {
  handleUndos.push(registerMentionHandle(nodeId, { insertImage, insertAsset }));
}
function releaseHandles() {
  while (handleUndos.length > 0) handleUndos.pop()?.();
  insertImage.mockReset();
  insertAsset.mockReset();
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/900000000000000001`]}>
      <Routes>
        <Route path="/team/:teamId/canvas/:canvasId" element={<LibraryPanel />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  global.ResizeObserver = ObserverStub as unknown as typeof ResizeObserver;
  global.IntersectionObserver = ObserverStub as unknown as typeof IntersectionObserver;
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
  _resetModelCapabilitiesCache();
  localStorage.clear();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
  useCanvasCoreStore.getState().reset();
  seedNodes([]);
  addToast.mockReset();
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  searchAssets.mockReset().mockResolvedValue([]);
  listAssets.mockReset().mockResolvedValue([]);
  fetchGenerated.mockReset().mockResolvedValue({ items: [], next_cursor: null });
  listGenerationCapabilities.mockReset().mockResolvedValue({
    'doubao-seedream': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 3,
      negative: true,
      video_modes: [],
    },
  });
  placeLibraryItems.mockReset().mockResolvedValue({
    nodeIds: ['media-1'], inserted: 1, skipped: 0, failed: [],
  });
  addReferences.mockReset().mockResolvedValue({ added: 1, skipped: 0, clamped: 0, failed: [] });
  importResourceAsCanvasMedia
    .mockReset()
    .mockResolvedValue({ url: '/api/v1/generated-media/gm-1/file', kind: 'image' });
  releaseHandles();
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
  releaseHandles();
  useCanvasCoreStore.getState().reset();
});

/** Open the panel on Assets and wait for the one row to land. */
async function openOnAssets(target?: { nodeId: string; kind: 'prompt'; title: string }) {
  searchAssets.mockResolvedValue([ASSET_ROW]);
  listAssets.mockResolvedValue([ASSET_ROW]);
  act(() => {
    useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'assets', target });
  });
  renderPanel();
  await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
}

/** Open the panel on Uploads and wait for the one row to land. */
async function openOnUploads(target?: { nodeId: string; kind: 'prompt'; title: string }) {
  act(() => {
    useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'uploads', target });
  });
  renderPanel();
  await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
}

describe('LibraryPanel', () => {
  it('is not in the tree at all until something opens it', () => {
    renderPanel();
    expect(screen.queryByTestId('library-panel')).toBeNull();
  });

  it('opens as a dialog on the canvas island', () => {
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    const panel = screen.getByTestId('library-panel');
    expect(panel.getAttribute('role')).toBe('dialog');
    // `canvas-island` is what gives it the floating chrome AND `nodrag
    // nowheel nopan` is what stops a drag inside it panning the board.
    expect(panel.className).toContain('canvas-island');
    expect(panel.className).toContain('nodrag');
    expect(screen.getByRole('dialog')).toBe(panel);
  });

  it('offers all three Media segments, one showing at a time', () => {
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    for (const s of ['assets', 'uploads', 'generated']) {
      expect(screen.getByTestId(`library-segment-${s}`)).toBeTruthy();
    }
    expect(screen.getByTestId('library-segment-assets').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByTestId('library-segment-generated'));
    expect(useLibraryStore.getState().mediaStore).toBe('generated');
  });

  it('the Files segment offers four source chips, All pressed', async () => {
    // The shelf lists every source_type in the scope — Douyin downloads,
    // uploads, derived cover frames, saved generations. It was labelled
    // "Uploads", which named a quarter of what it showed.
    await openOnUploads();
    for (const v of ['all', 'upload', 'web', 'generated']) {
      expect(screen.getByTestId(`library-source-${v}`)).toBeTruthy();
    }
    expect(
      screen.getByTestId('library-source-all').getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('a source chip reaches the wire, not just the button state', async () => {
    await openOnUploads();
    searchResources.mockClear();
    fireEvent.click(screen.getByTestId('library-source-web'));
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    expect(searchResources.mock.calls[0][0].sources).toBe('web');
    // Generated folds `derived` in: a cover frame cut from a video is neither
    // uploaded nor downloaded, and its own chip would name an internal
    // source_type nobody chose.
    searchResources.mockClear();
    fireEvent.click(screen.getByTestId('library-source-generated'));
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    expect(searchResources.mock.calls[0][0].sources).toBe('generated,derived');
  });

  it('the source row belongs to Files alone, and resets on the way back', async () => {
    await openOnUploads();
    fireEvent.click(screen.getByTestId('library-source-web'));
    fireEvent.click(screen.getByTestId('library-segment-generated'));
    expect(screen.queryByTestId('library-source-all')).toBeNull();
    fireEvent.click(screen.getByTestId('library-segment-uploads'));
    expect(
      screen.getByTestId('library-source-all').getAttribute('aria-pressed'),
    ).toBe('true');
    expect(
      screen.getByTestId('library-source-web').getAttribute('aria-pressed'),
    ).toBe('false');
  });

  it('the Assets shelf opens narrowed to the library, and says so', async () => {
    await openOnAssets();
    const toggle = screen.getByTestId('library-in-library-toggle');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(searchAssets).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ library: 'in' }),
    );
  });

  it('releasing the toggle re-queries wide, not just repaints the pill', async () => {
    await openOnAssets();
    searchAssets.mockClear();
    fireEvent.click(screen.getByTestId('library-in-library-toggle'));
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    expect(searchAssets.mock.calls[0][1].library).toBe('all');
    expect(
      screen.getByTestId('library-in-library-toggle').getAttribute('aria-pressed'),
    ).toBe('false');
  });

  it('an empty narrowed Assets shelf names the pill, not "you own nothing"', async () => {
    // The generic empty copy is a dead end here: the shelf is empty BECAUSE of
    // a pill three rows up, and nothing on screen connected the two. The
    // pointed copy is the only thing that makes the state recoverable.
    searchAssets.mockResolvedValue([]);
    listAssets.mockResolvedValue([]);
    act(() => {
      useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'assets' });
    });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('library-empty')).toBeTruthy());
    const empty = screen.getByTestId('library-empty').textContent ?? '';
    expect(empty).toContain('turn off In Library Only');
    expect(empty).not.toBe('Nothing Here Yet');
  });

  it('the WIDE Assets shelf keeps the plain empty copy — nothing to turn off', async () => {
    searchAssets.mockResolvedValue([]);
    listAssets.mockResolvedValue([]);
    act(() => {
      useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'assets' });
    });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('library-empty')).toBeTruthy());
    fireEvent.click(screen.getByTestId('library-in-library-toggle'));
    await waitFor(() =>
      expect(screen.getByTestId('library-empty').textContent).toBe('Nothing Here Yet'),
    );
  });

  it('an empty FILES shelf keeps the plain copy — the pill is not its control', async () => {
    // The hint names a control the Files shelf does not draw, so offering it
    // there would send the user looking for a pill that is not on screen.
    searchResources.mockResolvedValue({
      results: [],
      counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    act(() => {
      useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'uploads' });
    });
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId('library-empty').textContent).toBe('Nothing Here Yet'),
    );
  });

  it('the toggle belongs to Assets alone', async () => {
    await openOnUploads();
    expect(screen.queryByTestId('library-in-library-toggle')).toBeNull();
    fireEvent.click(screen.getByTestId('library-segment-generated'));
    expect(screen.queryByTestId('library-in-library-toggle')).toBeNull();
  });

  it('names the node it is aiming at, and says what will happen to it', async () => {
    seedNodes([promptNode()]);
    act(() => {
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' },
      });
    });
    renderPanel();
    expect(screen.getByTestId('library-target').textContent).toContain('Harbour at dusk');
    // The consequence line answers "and then what?" — which model, how much
    // room is left. A picker that only says "Harbour at dusk" does not.
    await waitFor(() =>
      expect(screen.getByTestId('library-consequence').textContent).toContain('doubao-seedream'),
    );
    expect(screen.getByTestId('library-consequence').textContent).toContain('0 / 3');
  });

  it('with no target the consequence line says what a plain pick does', () => {
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    expect(screen.queryByTestId('library-target')).toBeNull();
    expect(screen.getByTestId('library-consequence').textContent).toContain('Place on canvas');
  });

  it('the ✕ closes the panel and releases the target with it', () => {
    seedNodes([promptNode()]);
    act(() => {
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' },
      });
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('library-close'));
    expect(useLibraryStore.getState().open).toBe(false);
    expect(useLibraryStore.getState().target).toBeNull();
  });

  it('the target ✕ releases the target without closing the panel', () => {
    seedNodes([promptNode()]);
    act(() => {
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' },
      });
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('library-target-clear'));
    expect(useLibraryStore.getState().target).toBeNull();
    expect(useLibraryStore.getState().open).toBe(true);
  });

  it('Escape closes it', () => {
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    fireEvent.keyDown(screen.getByTestId('library-panel'), { key: 'Escape' });
    expect(useLibraryStore.getState().open).toBe(false);
  });

  it('a keystroke inside the panel does not reach the canvas, but Escape does', () => {
    const onWindow = vi.fn();
    window.addEventListener('keydown', onWindow);
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    fireEvent.keyDown(screen.getByTestId('library-panel'), { key: 'l' });
    expect(onWindow).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByTestId('library-panel'), { key: 'Escape' });
    expect(onWindow).toHaveBeenCalled();
    window.removeEventListener('keydown', onWindow);
  });

  it('the target releases itself when its node is deleted', async () => {
    act(() => {
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' },
      });
    });
    seedNodes([{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    renderPanel();
    expect(screen.getByTestId('library-target').textContent).toContain('Harbour');
    act(() => { seedNodes([]); });
    await waitFor(() => expect(screen.queryByTestId('library-target')).toBeNull());
    // Silently dropping the aim would leave the next Add landing nowhere.
    expect(addToast).toHaveBeenCalled();
  });

  it('Place on Canvas hands the chosen rows to placeLibraryItems', async () => {
    await openOnUploads();
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(placeLibraryItems).toHaveBeenCalledTimes(1));
    const [items, scopeId, position] = placeLibraryItems.mock.calls[0];
    expect((items as Array<{ id: string }>)[0].id).toBe(UPLOAD_ROW.id);
    expect(scopeId).toBe(SCOPE);
    // The world point under the viewport CENTRE, not null: `null` means "lay
    // them out in project lanes", which puts a card to the right of
    // everything already on the board — and the surface culls off-viewport
    // nodes, so the user would get a toast and an empty screen.
    expect(position).toEqual({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
    await waitFor(() => expect(useLibraryStore.getState().selection).toEqual([]));
  });

  it('the drop point follows the viewport, not the window', async () => {
    // Panned and zoomed, the centre of the SCREEN is a different point in the
    // world — which is the whole reason this goes through `screenToWorld`.
    act(() => {
      useCanvasCoreStore.setState({ viewport: { x: 100, y: 40, zoom: 2 } });
    });
    await openOnUploads();
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(placeLibraryItems).toHaveBeenCalledTimes(1));
    expect(placeLibraryItems.mock.calls[0][2]).toEqual({
      x: (window.innerWidth / 2 - 100) / 2,
      y: (window.innerHeight / 2 - 40) / 2,
    });
  });

  it('placing nothing because it is all already here still says so', async () => {
    placeLibraryItems.mockResolvedValue({ nodeIds: [], inserted: 0, skipped: 1, failed: [] });
    await openOnUploads();
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(String(addToast.mock.calls[0][0])).toContain('already on this canvas');
  });

  it('with a prompt target the primary action adds references, under its ceiling', async () => {
    seedNodes([promptNode()]);
    await openOnUploads({ nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' });
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addReferences).toHaveBeenCalledTimes(1));
    const [nodeId, items, scopeId, opts] = addReferences.mock.calls[0];
    expect(nodeId).toBe('p1');
    expect((items as Array<{ id: string }>)[0].id).toBe(UPLOAD_ROW.id);
    expect(scopeId).toBe(SCOPE);
    // The ceiling goes DOWN to the resolver: one asset can expand to several
    // refs, so a caller that slices its own pick list bounds picks, not refs.
    expect(opts).toEqual({ maxRefs: 3 });
  });

  it('a full node refuses the add and says why, rather than disabling in silence', async () => {
    seedNodes([
      promptNode([
        { url: '/api/v1/resources/1/cover', kind: 'image' },
        { url: '/api/v1/resources/2/cover', kind: 'image' },
        { url: '/api/v1/resources/3/cover', kind: 'image' },
      ]),
    ]);
    await openOnUploads({ nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' });
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    await waitFor(() =>
      expect(screen.getByTestId('library-note').textContent).toContain('3 / 3'),
    );
    expect((screen.getByTestId('library-primary') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('library-primary'));
    expect(addReferences).not.toHaveBeenCalled();
  });

  it('an add that fails is reported — a pick that changes nothing must say so', async () => {
    addReferences.mockResolvedValue({
      added: 0,
      skipped: 0,
      clamped: 0,
      failed: [{ item: { store: 'uploads', id: UPLOAD_ROW.id }, reason: 'mint_failed' }],
    });
    seedNodes([promptNode()]);
    await openOnUploads({ nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' });
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(String(addToast.mock.calls[0][0])).toContain('could not be added');
    // The selection SURVIVES a failure: clearing it would look like success.
    expect(useLibraryStore.getState().selection).toHaveLength(1);
  });

  it('a read-only canvas gets the shelf, and nothing that writes to the board', async () => {
    // `L` is view-only and fires in a read-only session, so the panel MUST
    // mount there — a key that toggles a store nobody renders is a silent
    // no-op. What must not survive is anything that would write: the board is
    // not the viewer's to change, and `markDirty` would swallow the attempt.
    act(() => { useCanvasCoreStore.setState({ readOnly: true }); });
    await openOnUploads();
    expect(screen.getByTestId('library-grid')).toBeTruthy();
    expect(screen.queryByTestId('library-primary')).toBeNull();
    expect(screen.queryByTestId('library-secondary')).toBeNull();
    expect(screen.getByTestId('library-consequence').textContent).toContain('read-only');
  });

  it('read-only says so in the consequence line rather than leaving it blank', async () => {
    act(() => { useCanvasCoreStore.setState({ readOnly: true }); });
    await openOnUploads();
    // A shelf with no buttons and no explanation reads as broken.
    expect(screen.getByTestId('library-consequence').textContent).toContain('Browse only');
  });

  it('read-only hides the target bar, even if a target somehow survived', () => {
    seedNodes([promptNode()]);
    act(() => {
      useCanvasCoreStore.setState({ readOnly: true });
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' },
      });
    });
    renderPanel();
    expect(screen.queryByTestId('library-target')).toBeNull();
  });

  it('the Prompts page is the one line P3 will replace', () => {
    act(() => { useLibraryStore.getState().openPanel(); });
    renderPanel();
    fireEvent.click(screen.getByTestId('library-page-prompts'));
    expect(screen.getByTestId('library-prompts-stub')).toBeTruthy();
    expect(screen.queryByTestId('library-grid')).toBeNull();
  });

  // ── "This Project" needs a project ────────────────────────────────────
  //
  // `fetchLibraryAssets` falls through to the scope-wide search when there is
  // no project id, so the chip could show PRESSED over every asset in the
  // workspace — a P1 label on a scope-wide list, which is the exact failure
  // Plan-time ruling 1 exists to prevent. The affordance this replaced had
  // the typed answer: the deleted `insertProjectAssets` toasted "This canvas
  // has no project".
  it('disables This Project on a canvas with none, and says why', async () => {
    seedNodes([]); // reset() leaves projectId null
    await openOnAssets();

    const chip = screen.getByTestId('library-scope-this-project');
    expect(chip).toBeDisabled();
    expect(chip.getAttribute('title')).toBe('This canvas has no project');
    // And the shelf is labelled for what it actually shows.
    expect(screen.getByTestId('library-scope-all').getAttribute('aria-pressed')).toBe('true');
  });

  it('asks the SCOPE-WIDE search when there is no project, whatever the stored scope says', async () => {
    // A scope left over from the last canvas must not mislabel this one.
    act(() => { useLibraryStore.getState().setAssetScope('this-project'); });
    await openOnAssets();

    expect(searchAssets).toHaveBeenCalled();
    expect(listAssets).not.toHaveBeenCalled();
    expect(screen.getByTestId('library-scope-this-project').getAttribute('aria-pressed')).toBe(
      'false',
    );
  });

  it('with a project the chip is live and asks the project-scoped list', async () => {
    useCanvasCoreStore.setState({ projectId: '900000000000000777' } as never);
    act(() => { useLibraryStore.getState().setAssetScope('this-project'); });
    await openOnAssets();

    expect(screen.getByTestId('library-scope-this-project')).not.toBeDisabled();
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ projectId: '900000000000000777' }),
    );
  });

  // ── The footer counts FILES ───────────────────────────────────────────
  it('counts files for picks whose file count is knowable', async () => {
    seedNodes([promptNode()]);
    await openOnUploads({ nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' });
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);

    await waitFor(() =>
      expect(screen.getByTestId('library-footer-count').textContent).toBe('1 selected · 1 files'),
    );
  });

  it('says only "selected" once an ASSET is picked — the file count is not knowable here', async () => {
    // One asset expands to one ref per primary-slot file, and only the detail
    // rows say how many that is. Printing "1 file" over a send of four is the
    // footer misstating delivery; the hover preview is where the per-asset
    // answer already lives.
    seedNodes([promptNode()]);
    await openOnAssets({ nodeId: 'p1', kind: 'prompt', title: 'Harbour at dusk' });
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);

    await waitFor(() =>
      expect(screen.getByTestId('library-footer-count').textContent).toBe('1 selected'),
    );
  });

  // The preview is `position: fixed` at a rect measured ONCE, when the pointer
  // arrived. Scroll the shelf and that rect describes a cell that has moved —
  // the card then floats beside whatever slid into its place, labelled with the
  // item that is no longer there. Dropping the hover is the honest answer: the
  // pointer's next move re-opens it against a fresh rect.
  it('a scroll drops the hover preview rather than leaving it pinned to a moved cell', async () => {
    // `shouldAdvanceTime`: the preview opens on a 400ms timer, and RTL's
    // `waitFor` polls on timers too — frozen fake timers would deadlock the
    // two against each other.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await openOnUploads();
      const cell = screen.getAllByTestId('library-cell')[0];
      fireEvent.mouseEnter(cell);
      await act(async () => {
        vi.advanceTimersByTime(PREVIEW_DELAY_MS + 50);
      });
      expect(screen.getByTestId('library-preview')).toBeTruthy();

      const scroller = screen.getByTestId('library-grid').querySelector('.overflow-y-auto');
      expect(scroller).toBeTruthy();
      fireEvent.scroll(scroller as Element);
      await waitFor(() => expect(screen.queryByTestId('library-preview')).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });

  it('Select All takes the whole shelf — the bulk gesture the chip merge dropped', async () => {
    await openOnUploads();
    fireEvent.click(screen.getByTestId('library-select-all'));
    expect(useLibraryStore.getState().selection).toEqual([`uploads:${UPLOAD_ROW.id}`]);
  });
});

// ── A Text-kind target commits MENTIONS, not references ─────────────────────
//
// The header's Open Library button (follow-up Task 3) can aim the panel at a
// prompt of ANY kind, and a text run sends `body` alone — `manual_refs` on a
// text node is dropped by the runner with nothing said. So when the aim is a
// text prompt the primary action writes chips into the body instead, which is
// the same result the `⌥`-drop already produces.

describe('LibraryPanel aimed at a Text-kind prompt', () => {
  const TEXT_TARGET = { nodeId: 'p1', kind: 'prompt' as const, title: 'Harbour at dusk' };

  it('offers Insert Mentions where a gen node offers Add References', async () => {
    // TWO rows, so the asserted string is the SHIPPED plural. At count 1
    // i18next resolves `insertMentions_one` — "Insert 1 Mention" — while the
    // stub `t` here renders the `defaultValue`, which is the _other_ form. The
    // one-row assertion therefore pinned a string the app never paints.
    searchResources.mockResolvedValue({
      results: [UPLOAD_ROW, SECOND_UPLOAD_ROW],
      counts: { all: 2, video: 0, image: 2, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    seedNodes([textPromptNode()]);
    armMentionHandle();
    act(() => {
      useLibraryStore.getState().openPanel({
        page: 'media', mediaStore: 'uploads', target: TEXT_TARGET,
      });
    });
    renderPanel();
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(2));
    fireEvent.click(screen.getByTestId('library-select-all'));
    expect(screen.getByTestId('library-primary').textContent).toContain('Insert 2 Mentions');
  });

  it('a gen node still offers Add References — the split is on `gen`, not on the panel', async () => {
    // Two rows for the same reason as the mention case above: the shipped
    // singular is `addReferences_one` — "Add 1 Reference" — and the stub `t`
    // would render the plural `defaultValue` at count 1.
    searchResources.mockResolvedValue({
      results: [UPLOAD_ROW, SECOND_UPLOAD_ROW],
      counts: { all: 2, video: 0, image: 2, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    seedNodes([promptNode()]);
    armMentionHandle();
    act(() => {
      useLibraryStore.getState().openPanel({
        page: 'media', mediaStore: 'uploads', target: TEXT_TARGET,
      });
    });
    renderPanel();
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(2));
    fireEvent.click(screen.getByTestId('library-select-all'));
    expect(screen.getByTestId('library-primary').textContent).toContain('Add 2 References');
  });

  it('inserting writes a chip through the node handle, and never a reference', async () => {
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(insertImage).toHaveBeenCalledTimes(1));
    // `consumeMention: false` is load-bearing: the default deletes back to the
    // last `@` within 80 characters of the caret, which is the `@` picker's
    // contract and destroys text on a path that has no pending query.
    expect(insertImage.mock.calls[0][1]).toEqual({ consumeMention: false });
    expect(insertImage.mock.calls[0][0]).toMatchObject({
      url: '/api/v1/generated-media/gm-1/file',
      alias: UPLOAD_ROW.name,
    });
    expect(addReferences).not.toHaveBeenCalled();
    // A commit that worked clears the pick, exactly as the reference path does
    // — otherwise a second click silently inserts the same chip again.
    await waitFor(() => expect(useLibraryStore.getState().selection).toEqual([]));
  });

  it('the target BAR says mentions too — not references over a mention button', async () => {
    // The bar is the line the user reads first. Left on "Adding references to
    // Harbour at dusk" over a button that inserts chips, it is the same class
    // of lie as a silent no-op: it describes an outcome the panel will not
    // produce. Both sites derive it from `isMentionTarget`.
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    const bar = screen.getByTestId('library-target').textContent ?? '';
    expect(bar).toContain('Inserting mentions into Harbour at dusk');
    expect(bar).not.toContain('Adding references');
  });

  it('an Image-kind target keeps the reference wording in that same bar', async () => {
    seedNodes([promptNode()]);
    await openOnUploads(TEXT_TARGET);
    const bar = screen.getByTestId('library-target').textContent ?? '';
    expect(bar).toContain('Adding references to Harbour at dusk');
    expect(bar).not.toContain('Inserting mentions');
  });

  it('says the consequence in the text prompt is chips, not reference images', async () => {
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    const line = screen.getByTestId('library-consequence').textContent ?? '';
    expect(line).toContain('Inserting mentions into Harbour at dusk');
    expect(line).toContain('not reference images');
  });

  it('with no editor registered it refuses OUT LOUD rather than doing nothing', async () => {
    // The card can be unmounted — the surface culls off-viewport nodes — and
    // an insert into an editor that is not there must not be a silent no-op.
    seedNodes([textPromptNode()]);
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(String(addToast.mock.calls[0][0])).toContain('Open the prompt node');
    expect(String(addToast.mock.calls[0][1])).toBe('error');
    expect(insertImage).not.toHaveBeenCalled();
    // The pick survives a refusal — clearing it would look like success.
    expect(useLibraryStore.getState().selection).toHaveLength(1);
  });

  it('a mention that fails is reported in the mention vocabulary', async () => {
    importResourceAsCanvasMedia.mockRejectedValue(new Error('mint down'));
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(String(addToast.mock.calls[0][0])).toContain('could not be inserted as a mention');
    expect(useLibraryStore.getState().selection).toHaveLength(1);
  });

  it('an inserter that throws reaches the TOAST, not a cleared pick', async () => {
    // `PromptNodeView` registers wrappers that throw when the body editor has
    // gone. Before that, they optional-chained the null away and returned
    // `undefined` — so the run counted a chip, the panel reported success and
    // cleared the pick, and the body was untouched. The whole point of the
    // throw is that this end of the path can speak.
    seedNodes([textPromptNode()]);
    armMentionHandle();
    insertImage.mockImplementation(() => {
      throw new Error('prompt body editor is not mounted');
    });
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    fireEvent.click(screen.getByTestId('library-primary'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    // Non-vacuous: the row DID resolve to a durable ref and the inserter WAS
    // reached. Without this the case would also pass if the refusal had
    // happened earlier, for a reason that has nothing to do with the editor.
    expect(insertImage).toHaveBeenCalled();
    expect(String(addToast.mock.calls[0][0])).toContain('could not be inserted as a mention');
    expect(String(addToast.mock.calls[0][1])).toBe('error');
    // The pick survives, so there is something to retry from.
    expect(useLibraryStore.getState().selection).toHaveLength(1);
  });

  it('double-clicking one row inserts that row alone', async () => {
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    fireEvent.doubleClick(screen.getAllByTestId('library-cell')[0]);
    await waitFor(() => expect(insertImage).toHaveBeenCalledTimes(1));
    expect(addReferences).not.toHaveBeenCalled();
  });

  it('drops the reference file count — a mention is not a reference send', async () => {
    seedNodes([textPromptNode()]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    // The footer's "N files" describes what the model receives as references.
    // On a text prompt that number is zero by construction, and printing it
    // would claim a send that never happens.
    const footer = screen.getByTestId('library-footer-count').textContent ?? '';
    expect(footer).toContain('1 selected');
    expect(footer).not.toContain('file');
  });

  it('leftover manual_refs on a switched-to-Text node do not raise a quota note', async () => {
    // Switching a card from Image to Text keeps whatever `manual_refs` it had.
    // Those are dead weight a text run ignores, so a "20 / 20 references used"
    // note over a mention shelf would be a true number about the wrong thing —
    // and the ceiling it enforces would disable an action that spends none of
    // that quota. Filled to `MAX_REFERENCE_IMAGES` on purpose: below it the
    // case passes whether or not the mention branch is exempt.
    const node = textPromptNode();
    (node.data as Record<string, unknown>).manual_refs = Array.from(
      { length: MAX_REFERENCE_IMAGES },
      (_, i) => ({ url: `/api/v1/resources/${i + 1}/cover`, kind: 'image' }),
    );
    seedNodes([node]);
    armMentionHandle();
    await openOnUploads(TEXT_TARGET);
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    expect(screen.queryByTestId('library-note')).toBeNull();
    expect((screen.getByTestId('library-primary') as HTMLButtonElement).disabled).toBe(false);
  });
});
