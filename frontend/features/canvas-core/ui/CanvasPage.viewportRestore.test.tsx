/**
 * `CanvasView` — applying a store-side viewport to React Flow's transform
 * (Task 3 fix round 1).
 *
 * Since the surface went uncontrolled, the store writing `viewport` no longer
 * moves anything: React Flow owns the transform and only ever hears about a
 * new one through an imperative `setViewport` on its instance. Four store-side
 * writes need that bridge — initial load, canvas switch, realtime rebase and
 * conflict resolve — and all four are routed through one `viewportEpoch`
 * counter so there is exactly one owner of "store viewport → canvas".
 *
 * The bug this file was written for (review C1): the first attempt keyed the
 * restore on a render-time `loadStatus` snapshot. On a canvas switch the
 * loader effect flips the store to `'loading'` synchronously, but the restore
 * effect in the same commit still closes over `'ready'` — so it latched the
 * NEW canvas id while applying the OLD canvas's viewport, and the re-run after
 * the new row landed returned early. Store held B, React Flow showed A, and
 * `TopNodeBar`/`CanvasComposer` placed the next node through a viewport that
 * was not on screen.
 *
 * The store here is the REAL one: `canvasService.getCanvas` is stubbed, so
 * `loadCanvas` → `applyServerRow` → epoch bump runs for real rather than
 * being simulated. A test that hand-bumped the epoch would prove only that
 * the effect reads a number.
 */

import React from 'react';
import { act, render, waitFor, cleanup } from '@testing-library/react';
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
const setViewport = vi.fn();
/** Withheld until a test asks for it — the "instance arrives late" case. */
let holdInit = false;
let releaseInit: (() => void) | null = null;
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      const fire = () => onInit?.({ fitView, setViewport });
      if (holdInit) releaseInit = fire;
      else fire();
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

const getCanvas = vi.fn();
const saveCanvas = vi.fn();
vi.mock('../services/canvasService', () => ({
  getCanvas: (...args: unknown[]) => getCanvas(...args),
  saveCanvas: (...args: unknown[]) => saveCanvas(...args),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { Canvas } from '../types';

const VIEWPORT_C1 = { x: 181.47, y: 106.68, zoom: 0.514 };
const VIEWPORT_C2 = { x: -900, y: 42, zoom: 1.75 };

function row(id: string, viewport: { x: number; y: number; zoom: number }): Canvas {
  return {
    id,
    project_id: null,
    name: id,
    kind: 'smart',
    viewport_json: viewport,
    nodes_json: [],
    connections_json: [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: '2026-09-02T10:00:00+00:00',
    created_at: '2026-09-02T09:00:00+00:00',
    updated_at: '2026-09-02T10:00:00+00:00',
    created_by: null,
  } as Canvas;
}

beforeEach(() => {
  holdInit = false;
  releaseInit = null;
  fitView.mockReset();
  setViewport.mockReset();
  saveCanvas.mockReset().mockResolvedValue({ ok: true, canvas: row('c1', VIEWPORT_C1) });
  getCanvas.mockReset().mockImplementation(async (id: string) =>
    row(id, id === 'c2' ? VIEWPORT_C2 : VIEWPORT_C1),
  );
  useCanvasCoreStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('CanvasView — persisted viewport reaches React Flow', () => {
  it('applies the loaded canvas viewport once the row lands', async () => {
    render(<CanvasView canvasId="c1" />);

    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));
    expect(setViewport).toHaveBeenCalledWith(VIEWPORT_C1);
  });

  it('applies the NEW canvas viewport on a switch, exactly once', async () => {
    // `CanvasView` is not remounted when `:canvasId` changes — it re-renders
    // with a new prop. This is the case review C1 fired on: keyed on a stale
    // `loadStatus`, the effect applied c1's viewport under c2's id and then
    // latched c2 out of ever being restored.
    const { rerender } = render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));

    rerender(<CanvasView canvasId="c2" />);

    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(2));
    expect(setViewport).toHaveBeenLastCalledWith(VIEWPORT_C2);
    expect(useCanvasCoreStore.getState().viewport).toEqual(VIEWPORT_C2);
  });

  it('applies a realtime rebase — the store must not adopt a viewport the canvas is not showing', async () => {
    // Review I1: `applyRemoteUpdate`'s happy path writes the store viewport.
    // Uncontrolled, that silently desynchronises the two unless it is bridged.
    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));

    act(() => {
      useCanvasCoreStore.getState().applyRemoteUpdate(
        {
          ...row('c1', { x: 7, y: 8, zoom: 2 }),
          base_updated_at: '2026-09-02T11:00:00+00:00',
        },
      );
    });

    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(2));
    expect(setViewport).toHaveBeenLastCalledWith({ x: 7, y: 8, zoom: 2 });
  });

  it('applies a conflict resolve', async () => {
    // Review I1, second call site: `resolveConflictWithServer`.
    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));

    act(() => {
      useCanvasCoreStore.setState({
        conflict: {
          ...row('c1', { x: -1, y: -2, zoom: 3 }),
          base_updated_at: '2026-09-02T12:00:00+00:00',
        },
      });
      useCanvasCoreStore.getState().resolveConflictWithServer();
    });

    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(2));
    expect(setViewport).toHaveBeenLastCalledWith({ x: -1, y: -2, zoom: 3 });
  });

  it('does not re-apply while the user pans — a settled viewport is already on screen', async () => {
    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));

    act(() => {
      useCanvasCoreStore.getState().setViewportSettled({ x: 5, y: 5, zoom: 1.1 });
      useCanvasCoreStore.getState().setViewportSettled({ x: 9, y: 9, zoom: 1.2 });
    });

    // Yanking the canvas back to a viewport it is already showing is the
    // failure mode a naive "watch the store viewport" effect produces.
    await Promise.resolve();
    expect(setViewport).toHaveBeenCalledTimes(1);
  });

  it('applies on init when the instance was not ready at bump time', async () => {
    holdInit = true;
    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(useCanvasCoreStore.getState().loadStatus).toBe('ready'));
    expect(setViewport).not.toHaveBeenCalled();

    act(() => {
      releaseInit?.();
    });

    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));
    expect(setViewport).toHaveBeenCalledWith(VIEWPORT_C1);
  });

  it('ignores an epoch belonging to a different canvas', async () => {
    render(<CanvasView canvasId="c1" />);
    await waitFor(() => expect(setViewport).toHaveBeenCalledTimes(1));

    // A late row for a canvas this view is no longer showing must not move it.
    act(() => {
      useCanvasCoreStore.setState({ canvasId: 'somewhere-else' });
      useCanvasCoreStore.getState().applyRemoteUpdate(
        {
          ...row('somewhere-else', { x: 99, y: 99, zoom: 4 }),
          base_updated_at: '2026-09-02T13:00:00+00:00',
        },
      );
    });

    await Promise.resolve();
    expect(setViewport).toHaveBeenCalledTimes(1);
  });
});
