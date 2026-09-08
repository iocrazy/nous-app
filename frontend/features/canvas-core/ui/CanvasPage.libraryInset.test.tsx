/**
 * The surface root publishes how much room the Library panel is taking.
 *
 * The panel floats over the BOARD on purpose, but the canvas chrome is not
 * the board: with the panel open, the composer (`inset-x-0 mx-auto`), the
 * save badge and the Arrange button (`right-4`) were all still centring and
 * anchoring against the full surface, so they ended up underneath it. The
 * reservation is derived once (`libraryInset`) and handed to every island as
 * a CSS custom property on this root — this file is what says the wire is
 * actually connected, and that it goes back to zero when the panel closes.
 */

import React from 'react';
import { render, cleanup, screen, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SCOPE = '727145299382534100';

vi.mock('react-router-dom', () => ({
  useParams: () => ({ canvasId: 'c1', teamId: SCOPE }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast: vi.fn() }),
  useToast: () => ({ addToast: vi.fn() }),
}));
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView: vi.fn(), setViewport: vi.fn() });
    }, [onInit]);
    return <div data-testid="canvas-surface" />;
  },
}));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('../realtime/useCanvasRealtime', () => ({ useCanvasRealtime: () => {} }));
vi.mock('./TopNodeBar', () => ({ TopNodeBar: () => <div data-testid="top-node-bar" /> }));
vi.mock('../library/LibraryPanel', () => ({
  LibraryPanel: () => <div data-testid="library-panel" />,
}));
vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: vi.fn().mockResolvedValue({ data: [] }),
}));
vi.mock('../../../editor/sceneService', () => ({
  listScenes: vi.fn().mockResolvedValue([]),
  listShots: vi.fn().mockResolvedValue([]),
  createShot: vi.fn(),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useLibraryStore } from '../library/libraryStore';
import { INSET_BOTTOM_VAR, INSET_RIGHT_VAR, ISLAND_GAP, PANEL_GUTTER } from '../library/libraryInset';

function seedReady(kind: string) {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind,
    projectId: 'proj-1',
    nodes: [],
    connections: [],
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
  } as never);
}

/** The surface root is the only positioned ancestor the islands share. */
function surfaceRoot(): HTMLElement {
  const el = screen.getByTestId('canvas-surface').parentElement;
  if (!el) throw new Error('canvas surface has no root');
  return el;
}

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});
afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});

describe('the canvas surface publishes the Library reservation', () => {
  it('reserves nothing while the panel is closed', () => {
    seedReady('smart');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);

    const root = surfaceRoot();
    expect(root.style.getPropertyValue(INSET_RIGHT_VAR)).toBe('0px');
    expect(root.style.getPropertyValue(INSET_BOTTOM_VAR)).toBe('0px');
  });

  it('reserves the panel width once it opens, and gives it back on close', () => {
    seedReady('smart');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);

    act(() => {
      useLibraryStore.getState().openPanel({ page: 'prompts' });
    });
    // The Prompts page is the wide one — 600 — and it is the page the
    // reported overlap was taken on.
    expect(surfaceRoot().style.getPropertyValue(INSET_RIGHT_VAR)).toBe(
      `${600 + PANEL_GUTTER + ISLAND_GAP}px`,
    );

    act(() => {
      useLibraryStore.getState().setPage('media');
    });
    // Narrower page, narrower reservation: a constant here would strand the
    // composer 260px from where the panel actually ends.
    expect(surfaceRoot().style.getPropertyValue(INSET_RIGHT_VAR)).toBe(
      `${340 + PANEL_GUTTER + ISLAND_GAP}px`,
    );

    act(() => {
      useLibraryStore.getState().close();
    });
    expect(surfaceRoot().style.getPropertyValue(INSET_RIGHT_VAR)).toBe('0px');
  });

  it('the save badge stops beside the panel too', () => {
    // The badge is top-RIGHT — the corner the docked panel takes first. It is
    // driven here through `readOnly`, the one state that shows it
    // unconditionally.
    seedReady('smart');
    useCanvasCoreStore.setState({ readOnly: true } as never);
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);

    const badge = screen.getByRole('status');
    expect(badge.className).toContain(`var(${INSET_RIGHT_VAR}`);
    expect(badge.className).not.toMatch(/\bright-4\b/);
  });

  it('publishes the variables on a canvas that never mounts the panel too', () => {
    // A storyboard has no Library, so the reservation is permanently zero —
    // but the variables still have to EXIST, or every island's
    // `var(--canvas-inset-right)` would fall back per-declaration and the
    // fallback would be the only thing holding the layout together.
    seedReady('storyboard');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);

    const root = surfaceRoot();
    expect(root.style.getPropertyValue(INSET_RIGHT_VAR)).toBe('0px');
    expect(root.style.getPropertyValue(INSET_BOTTOM_VAR)).toBe('0px');
  });
});
