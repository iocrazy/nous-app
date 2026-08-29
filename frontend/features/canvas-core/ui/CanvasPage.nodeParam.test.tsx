/**
 * `CanvasView`'s `?node=<id>` reader — the landing half of the Generated
 * inbox's deep link (`/team/{scope}/canvas/{id}?node=n9`). A card's source
 * line is only worth clicking if it puts you in front of the node that made
 * the image, so this selects it and centres the viewport on it.
 *
 * Mocks copied from `CanvasPage.focus.test.tsx` — same harness, one extra
 * knob: `useSearchParams` is driven per test.
 */

import React from 'react';
import { act, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let searchParams = new URLSearchParams();
vi.mock('react-router-dom', () => ({
  useParams: () => ({ canvasId: 'c1' }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [searchParams],
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
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

const fetchScriptProjects = vi.fn();
vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: (...args: unknown[]) => fetchScriptProjects(...args),
}));

const listScenes = vi.fn();
const listShots = vi.fn();
const createShot = vi.fn();
vi.mock('../../../editor/sceneService', () => ({
  listScenes: (...args: unknown[]) => listScenes(...args),
  listShots: (...args: unknown[]) => listShots(...args),
  createShot: (...args: unknown[]) => createShot(...args),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const seedReady = (nodes: unknown[]) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind: 'smart',
    projectId: 'proj-1',
    episodeId: 'ep-1',
    nodes: nodes as never,
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
  } as never);
};

beforeEach(() => {
  searchParams = new URLSearchParams();
  useCanvasCoreStore.getState().reset();
  fetchScriptProjects.mockReset().mockResolvedValue({ data: [] });
  listScenes.mockReset().mockResolvedValue([]);
  listShots.mockReset().mockResolvedValue([]);
  createShot.mockReset();
  fitView.mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CanvasView — ?node= deep-link reader', () => {
  it('selects the named node and centres the viewport on it', async () => {
    searchParams = new URLSearchParams('node=n9');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({ nodes: [{ id: 'n9' }] });
    expect(useCanvasCoreStore.getState().selection).toEqual(['n9']);
  });

  it('moves again when a second inbox card changes only the query string', async () => {
    // The case the latch exists to PERMIT, and the one a bare
    // same-props rerender could never exercise: two cards on the same
    // canvas differ by `?node=` alone.
    searchParams = new URLSearchParams('node=n9');
    seedReady([
      { id: 'n9', type: 'image', data: {} },
      { id: 'n10', type: 'image', data: {} },
    ]);

    const { rerender } = render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({ nodes: [{ id: 'n9' }] });

    searchParams = new URLSearchParams('node=n10');
    rerender(<CanvasView canvasId="c1" />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(2));
    expect(fitView.mock.calls[1][0]).toMatchObject({ nodes: [{ id: 'n10' }] });
    expect(useCanvasCoreStore.getState().selection).toEqual(['n10']);
  });

  it('returning to a node already visited moves the viewport again', async () => {
    searchParams = new URLSearchParams('node=n9');
    seedReady([
      { id: 'n9', type: 'image', data: {} },
      { id: 'n10', type: 'image', data: {} },
    ]);

    const { rerender } = render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    searchParams = new URLSearchParams('node=n10');
    rerender(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(2));

    // The latch remembers only the last key, so going back is a real
    // navigation, not a no-op.
    searchParams = new URLSearchParams('node=n9');
    rerender(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(3));
    expect(fitView.mock.calls[2][0]).toMatchObject({ nodes: [{ id: 'n9' }] });
  });

  it('the same node id on a DIFFERENT canvas is not swallowed by the latch', async () => {
    // `CanvasView` is not remounted when the route's `:canvasId` changes —
    // it re-renders with a new prop and the ref survives. A latch keyed on
    // the param alone would make this navigation a silent no-op.
    searchParams = new URLSearchParams('node=n9');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    const { rerender } = render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    rerender(<CanvasView canvasId="c2" />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(2));
    expect(fitView.mock.calls[1][0]).toMatchObject({ nodes: [{ id: 'n9' }] });
  });

  it('does NOT re-centre when the canvas reloads in place at the same key', async () => {
    // The test the latch itself is accountable to, and it has to drive a
    // REAL effect re-run to be worth anything: a same-props rerender changes
    // no dep, so React would not re-run the effect even with the latch
    // deleted — such a test passes either way and proves nothing. An
    // in-place reload does change one (`loadStatus` ready → loading →
    // ready) while `canvasId|node` stays put. Without the latch that yanks
    // the viewport back to the deep-linked node after the user has panned
    // away.
    searchParams = new URLSearchParams('node=n9');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));

    await act(async () => {
      useCanvasCoreStore.setState({ loadStatus: 'loading' } as never);
    });
    await act(async () => {
      useCanvasCoreStore.setState({ loadStatus: 'ready' } as never);
    });

    expect(fitView).toHaveBeenCalledTimes(1);
  });

  it('warns and does nothing for an id the canvas does not have', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    searchParams = new URLSearchParams('node=nope');
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
    await waitFor(() => expect(warn).toHaveBeenCalled());
  });

  it('does nothing at all without the param', async () => {
    seedReady([{ id: 'n9', type: 'image', data: {} }]);

    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    await act(async () => {
      await Promise.resolve();
    });

    expect(fitView).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
  });
});
