/**
 * `CanvasView`'s empty-viewport self-heal (2026-08-12 production incident).
 *
 * Production canvas 337610660408263 stores
 * `viewport_json = {x:181.47, y:106.68, zoom:0.514}` while all six of its
 * de-duplicated shot nodes sit in one column at x=2240 — outside the world
 * rect that viewport frames (x≈-352..2138). `CanvasEngine` renders with
 * `onlyRenderVisibleElements`, so those nodes never even enter the DOM: the
 * user opens the storyboard canvas and sees an empty grid with content
 * visible only in the minimap.
 *
 * The rules pinned here:
 *   - saved viewport frames NOTHING but nodes exist  → one `fitView()`
 *   - saved viewport frames something               → left exactly as saved
 *   - zero nodes                                    → never touched
 *   - the heal must not dirty the document          → no save is scheduled
 *
 * jsdom reports 0×0 for every element, so the surface size — the one input
 * the criterion cannot be evaluated without — is stubbed explicitly per
 * test. That is also why the un-stubbed default is "skip the heal": an
 * unmeasurable surface means we do not know what is framed.
 */

import React from 'react';
import { act, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({
  useParams: () => ({ canvasId: 'c1' }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams()],
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fallback?: string) => fallback ?? k }),
}));

const fitView = vi.fn();
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView });
    }, [onInit]);
    return <div data-testid="canvas-surface" />;
  },
}));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('../realtime/useCanvasRealtime', () => ({ useCanvasRealtime: () => {} }));
vi.mock('./useCanvasShortcuts', () => ({ useCanvasShortcuts: () => {} }));

vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: vi.fn().mockResolvedValue({ data: [] }),
}));
vi.mock('../../../editor/sceneService', () => ({
  listScenes: vi.fn().mockResolvedValue([]),
  listShots: vi.fn().mockResolvedValue([]),
  createShot: vi.fn(),
}));

import { CanvasView, VIEWPORT_HEAL_MIN_ZOOM } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

/**
 * React Flow's own fit-zoom arithmetic, reproduced from
 * `@xyflow/system`'s `getViewportForBounds` (numeric padding resolves to
 * `padding * width` per side, so `2 * padding * width` total on each axis):
 *
 *   zoom = clamp(min((w - 2pw) / bounds.w, (h - 2ph) / bounds.h), min, max)
 *
 * The `fitView` spy can't tell us what zoom React Flow WOULD land on, so the
 * tall-thin assertion below evaluates the same formula against the options
 * the component actually passed. That is what makes "the floor is not merely
 * present, it BINDS for the shape that caused the report" checkable.
 */
function fitZoom(
  bounds: { width: number; height: number },
  surface: { width: number; height: number },
  opts: { padding: number; minZoom?: number; maxZoom?: number },
): number {
  const xZoom = (surface.width - 2 * opts.padding * surface.width) / bounds.width;
  const yZoom = (surface.height - 2 * opts.padding * surface.height) / bounds.height;
  return Math.min(
    Math.max(Math.min(xZoom, yZoom), opts.minZoom ?? 0.1),
    opts.maxZoom ?? 4,
  );
}

/** The real row's viewport, verbatim. */
const PROD_VIEWPORT = { x: 181.47, y: 106.68, zoom: 0.514 };
/** The real row's shot nodes after `dedupeNodesById` — one column at x=2240. */
const OFFSCREEN_NODES = [0, 400, 800, 1200, 1600, 2000].map((y, i) => ({
  id: `shot-33761066040830${i}`,
  type: 'shot',
  position: { x: 2240, y },
  data: { shot_id: `33761066040830${i}`, label: `Shot ${i + 1}` },
}));

/** jsdom gives every element a 0×0 box; the heal needs a real surface size. */
function stubSurfaceSize(width: number, height: number): void {
  vi.spyOn(HTMLDivElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect);
}

const seedReady = (opts: {
  viewport: { x: number; y: number; zoom: number };
  nodes: unknown[];
  kind?: string;
}) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind: opts.kind ?? 'storyboard',
    projectId: '337610660408111',
    episodeId: null, // no reconcile fan-out — this file only tests the heal
    viewport: opts.viewport,
    nodes: opts.nodes as never,
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  } as never);
};

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  fitView.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('CanvasView — empty-viewport self-heal', () => {
  it('fits the view when the saved viewport frames no node (the production row)', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: OFFSCREEN_NODES });

    render(<CanvasView canvasId="c1" />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({
      padding: 0.2,
      minZoom: VIEWPORT_HEAL_MIN_ZOOM,
    });
  });

  it('floors the fit zoom — tall-thin content overflows rather than shrinking to a smear', async () => {
    // The reported shape: one scene column of shot cards, ~200 world px wide
    // and ~2400 tall, in a 1280×720 surface. Unfloored this fits at ~0.18 —
    // every card on screen, none of them readable (12px description → 2px).
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: OFFSCREEN_NODES });

    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    const opts = fitView.mock.calls[0][0] as { padding: number; minZoom: number };
    const bounds = { width: 200, height: 2400 };
    const surface = { width: 1280, height: 720 };

    // Precondition: without a floor this content really does collapse — the
    // assertion below would be vacuous if it didn't.
    expect(fitZoom(bounds, surface, { padding: opts.padding })).toBeLessThan(
      VIEWPORT_HEAL_MIN_ZOOM,
    );
    // With the options actually passed, the heal cannot go below the floor.
    expect(fitZoom(bounds, surface, opts)).toBe(VIEWPORT_HEAL_MIN_ZOOM);
  });

  it('does not zoom IN past the natural fit — the floor is a floor, not a target', async () => {
    // Content that comfortably fits keeps its own (larger) fit zoom; the
    // floor must never pull a well-framed board back down to 0.7.
    const opts = { padding: 0.2, minZoom: VIEWPORT_HEAL_MIN_ZOOM };
    expect(fitZoom({ width: 400, height: 300 }, { width: 1280, height: 720 }, opts)).toBeGreaterThan(
      VIEWPORT_HEAL_MIN_ZOOM,
    );
  });

  it('heals at most once, even as the node list churns afterwards', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: OFFSCREEN_NODES });

    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    act(() => {
      useCanvasCoreStore.setState({
        nodes: [...OFFSCREEN_NODES, { id: 'shot-new', position: { x: 2240, y: 2400 } }] as never,
      });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(fitView).toHaveBeenCalledTimes(1);
  });

  it('leaves a viewport that DOES frame content exactly as the user saved it', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({
      viewport: PROD_VIEWPORT,
      nodes: [{ id: 'shot-1', type: 'shot', position: { x: 100, y: 100 }, data: {} }],
    });

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().viewport).toEqual(PROD_VIEWPORT);
  });

  it('never touches an empty canvas (0 nodes is not a broken viewport)', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: [] });

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
  });

  it('arms only once nodes arrive — a canvas whose shot nodes land after mount still heals', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: [] });

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    expect(fitView).not.toHaveBeenCalled();

    act(() => {
      useCanvasCoreStore.setState({ nodes: OFFSCREEN_NODES as never });
    });
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
  });

  it('skips (without latching) while the surface is unmeasurable — 0×0 tells us nothing', async () => {
    // No size stub: jsdom's default 0×0, e.g. a hidden storyboard tab.
    seedReady({ viewport: PROD_VIEWPORT, nodes: OFFSCREEN_NODES });

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });
    expect(fitView).not.toHaveBeenCalled();

    // Once it CAN be measured, the next node-list change re-evaluates.
    stubSurfaceSize(1280, 720);
    act(() => {
      useCanvasCoreStore.setState({
        nodes: [...OFFSCREEN_NODES, { id: 'shot-x', position: { x: 2240, y: 2400 } }] as never,
      });
    });
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
  });

  it('the heal is view-only: it dirties nothing and schedules no save', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: PROD_VIEWPORT, nodes: OFFSCREEN_NODES });
    const revisionBefore = useCanvasCoreStore.getState().revision;

    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    const s = useCanvasCoreStore.getState();
    // `fitView()` moves React Flow's own transform; the store learns about it
    // through `onMove` → `setViewportOnMove`, which never bumps revision.
    expect(s.revision).toBe(revisionBefore);
    expect(s.saveStatus).toBe('idle');
    // The persisted viewport is untouched by the heal itself.
    expect(s.viewport).toEqual(PROD_VIEWPORT);
  });
});

describe('CanvasView — read-only badge', () => {
  it('renders "Read-only" instead of "Save failed" once the store latches', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: { x: 0, y: 0, zoom: 1 }, nodes: [] });
    act(() => {
      useCanvasCoreStore.setState({ readOnly: true, saveStatus: 'idle', saveError: null });
    });

    render(<CanvasView canvasId="c1" />);

    expect(await screen.findByText('Read-only')).toBeInTheDocument();
    expect(screen.queryByText('Save failed')).toBeNull();
  });

  it('a writable canvas with a real save error still shows "Save failed"', async () => {
    stubSurfaceSize(1280, 720);
    seedReady({ viewport: { x: 0, y: 0, zoom: 1 }, nodes: [] });
    act(() => {
      useCanvasCoreStore.setState({
        readOnly: false,
        saveStatus: 'error',
        saveError: 'HTTP 500',
      });
    });

    render(<CanvasView canvasId="c1" />);

    expect(await screen.findByText('Save failed')).toBeInTheDocument();
    expect(screen.queryByText('Read-only')).toBeNull();
  });
});
