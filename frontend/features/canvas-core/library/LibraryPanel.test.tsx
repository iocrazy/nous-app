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

import { _resetModelCapabilitiesCache } from '../smart/nodes/useModelCapabilities';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { LibraryPanel } from './LibraryPanel';
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
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
  useCanvasCoreStore.getState().reset();
});

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

  it('Select All takes the whole shelf — the bulk gesture the chip merge dropped', async () => {
    await openOnUploads();
    fireEvent.click(screen.getByTestId('library-select-all'));
    expect(useLibraryStore.getState().selection).toEqual([`uploads:${UPLOAD_ROW.id}`]);
  });
});
