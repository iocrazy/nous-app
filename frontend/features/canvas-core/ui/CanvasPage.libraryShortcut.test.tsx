/**
 * `L` on a canvas that does not mount the Library panel.
 *
 * The key was wired unconditionally while the panel mounts only for the smart
 * family, so on a storyboard (`StoryboardCanvasEmbed` renders this same
 * `CanvasView`) or a classic board the keystroke was swallowed: nothing on
 * screen, no error — AND `useLibraryStore` is a module singleton, so `open`
 * stayed true and the next smart canvas opened in the same session showed the
 * panel unbidden. A silent no-op with a delayed visible consequence.
 *
 * This file exists rather than a case in `CanvasPage.topNodeBar.test.tsx`
 * because that one stubs `useCanvasShortcuts` to a no-op, which is exactly
 * the wiring under test here. The real hook is used, so a real `keydown`
 * proves the gate rather than proving the mock.
 */

import React from 'react';
import { render, cleanup, screen, fireEvent } from '@testing-library/react';
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

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});
afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});

describe('the L shortcut is bound only where the panel mounts', () => {
  it('opens the panel on a smart canvas', () => {
    seedReady('smart');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);
    expect(screen.getByTestId('library-panel')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'l' });

    expect(useLibraryStore.getState().open).toBe(true);
  });

  it('does NOTHING on a storyboard, which never mounts the panel', () => {
    seedReady('storyboard');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);
    expect(screen.queryByTestId('library-panel')).toBeNull();

    fireEvent.keyDown(window, { key: 'l' });

    // The state matters more than the absent panel: this store outlives the
    // canvas, so a `true` here reopens on the NEXT smart board.
    expect(useLibraryStore.getState().open).toBe(false);
  });

  it('does nothing on a classic canvas either', () => {
    seedReady('classic');
    render(<CanvasView canvasId="c1" teamId={SCOPE} />);

    fireEvent.keyDown(window, { key: 'l' });

    expect(useLibraryStore.getState().open).toBe(false);
  });
});
