/**
 * CanvasPage empty-state guard.
 *
 * A ready canvas with zero nodes used to render a silently-blank CanvasSurface
 * (an empty React Flow grid), which read as a broken/loading page. It now
 * renders an explicit centered empty state instead. A ready canvas WITH nodes
 * still renders the surface as before.
 */

import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({
  useParams: () => ({ canvasId: 'c1' }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Heavy React Flow surface + overlays are stubbed to simple markers so the
// test isolates CanvasPage's branch logic (surface vs empty state).
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: () => <div data-testid="canvas-surface" />,
}));
vi.mock('../classic/ui/ClassicPalette', () => ({ ClassicPalette: () => null }));
vi.mock('../classic/ui/ClassicRunBar', () => ({ ClassicRunBar: () => null }));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('../realtime/useCanvasRealtime', () => ({ useCanvasRealtime: () => {} }));
vi.mock('./useCanvasShortcuts', () => ({ useCanvasShortcuts: () => {} }));

import CanvasPage from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const seedReady = (nodes: unknown[]) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    kind: 'classic',
    nodes: nodes as never,
    // Neutralise the mount effect so it never hits the network.
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  } as never);
};

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CanvasPage empty state', () => {
  it('renders an explicit empty state (not the blank surface) when ready with zero nodes', () => {
    seedReady([]);
    render(<CanvasPage />);
    expect(screen.getByText('canvas.empty.title')).toBeTruthy();
    expect(screen.queryByTestId('canvas-surface')).toBeNull();
  });

  it('renders the canvas surface when ready with nodes', () => {
    seedReady([{ id: 'n1' }]);
    render(<CanvasPage />);
    expect(screen.getByTestId('canvas-surface')).toBeTruthy();
    expect(screen.queryByText('canvas.empty.title')).toBeNull();
  });
});
