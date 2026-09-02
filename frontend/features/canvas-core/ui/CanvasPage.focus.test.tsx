/**
 * `CanvasView`'s Task 5 additions: the `focusShotId` viewport-focus prop
 * (used by the embedded storyboard-page Canvas tab) and the `onBack`-gated
 * "back to canvases" pill (hidden when embedded, shown for the real
 * `/canvas/:id` route). The reconcile pass itself is covered by
 * `CanvasPage.shotSync.test.tsx` — this file only adds the ordering
 * guarantee that focus waits for reconcile to settle before searching the
 * node list.
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
  useTranslation: () => ({ t: (k: string) => k }),
}));

// A fake React Flow instance — the mock CanvasSurface below hands it to
// `onInit` the same way the real `CanvasEngine` does, letting these tests
// assert on `fitView` calls without mounting real React Flow.
const fitView = vi.fn();
// The real engine exposes this too — `CanvasView` calls it to restore the
// persisted viewport on open and on canvas switch (uncontrolled since Task 3).
const setViewport = vi.fn();
vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView, setViewport });
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

import CanvasPage, { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const seedReady = (
  overrides: Partial<{ kind: string; nodes: unknown[]; canvasId: string }> = {},
) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: overrides.canvasId ?? 'c1',
    kind: overrides.kind ?? 'storyboard',
    projectId: 'proj-1',
    episodeId: 'ep-1',
    nodes: (overrides.nodes ?? []) as never,
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  } as never);
};

beforeEach(() => {
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

describe('CanvasView — focusShotId viewport focus (Task 5)', () => {
  it('fitViews to shot-{id} once the node already exists and reconcile settles', async () => {
    seedReady({ nodes: [{ id: 'shot-9007199254740997', type: 'shot', data: {} }] });
    const onFocusHandled = vi.fn();

    render(
      <CanvasView canvasId="c1" focusShotId="9007199254740997" onFocusHandled={onFocusHandled} />,
    );

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({
      nodes: [{ id: 'shot-9007199254740997' }],
    });
    await waitFor(() => expect(onFocusHandled).toHaveBeenCalledTimes(1));
  });

  it('waits for reconcile to add the shot node before focusing it (ordering guarantee)', async () => {
    // The node does NOT exist yet — reconcile must add it first. A script +
    // one scene + one shot resolves, so `reconcileShotNodes` produces a
    // `nodesToAdd` entry for shot 'db-1' → node id `shot-db-1`.
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([
      { id: 'scene-1', script_id: 'script-1', chapter_id: null, heading_int_ext: 'INT',
        location_text: 'Kitchen', time_of_day: 'DAY', content_version: 1, elements: [], sort_order: 1 },
    ]);
    listShots.mockResolvedValue([
      { id: 'db-1', scene_id: 'scene-1', shot_number: 1, shot_type: 'MEDIUM', camera_angle: 'EYE_LEVEL',
        camera_movement: 'STATIC', focal_length: '35mm', lighting: null, description: '',
        image_url: null, thumbnail_url: null, video_url: null, status: 'empty', sort_order: 1000 },
    ]);
    seedReady({ nodes: [] });
    const onFocusHandled = vi.fn();

    render(<CanvasView canvasId="c1" focusShotId="db-1" onFocusHandled={onFocusHandled} />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    expect(fitView.mock.calls[0][0]).toMatchObject({ nodes: [{ id: 'shot-db-1' }] });
    expect(onFocusHandled).toHaveBeenCalledTimes(1);
  });

  it('a shot id with no matching node settles without calling fitView, but still reports handled', async () => {
    seedReady({ nodes: [] });
    const onFocusHandled = vi.fn();

    render(
      <CanvasView canvasId="c1" focusShotId="does-not-exist" onFocusHandled={onFocusHandled} />,
    );

    await waitFor(() => expect(onFocusHandled).toHaveBeenCalledTimes(1));
    expect(fitView).not.toHaveBeenCalled();
  });

  it('a non-storyboard canvas focuses immediately (no reconcile to wait for)', async () => {
    seedReady({ kind: 'smart', nodes: [{ id: 'shot-x', type: 'shot', data: {} }] });
    const onFocusHandled = vi.fn();

    render(<CanvasView canvasId="c1" focusShotId="x" onFocusHandled={onFocusHandled} />);

    await waitFor(() => expect(fitView).toHaveBeenCalledTimes(1));
    // No script/scene/shot fetch fan-out for a non-storyboard canvas.
    expect(fetchScriptProjects).not.toHaveBeenCalled();
  });

  it('no focusShotId prop never calls fitView or onFocusHandled', async () => {
    seedReady({ nodes: [{ id: 'shot-1', type: 'shot', data: {} }] });
    const onFocusHandled = vi.fn();

    render(<CanvasView canvasId="c1" onFocusHandled={onFocusHandled} />);
    await screen.findByTestId('canvas-surface');

    // Let any pending microtasks flush.
    await act(async () => {
      await Promise.resolve();
    });
    expect(fitView).not.toHaveBeenCalled();
    expect(onFocusHandled).not.toHaveBeenCalled();
  });
});

describe('CanvasView — onBack-gated back pill (Task 5 embedded mode)', () => {
  it('embedded mode (no onBack) renders no back pill', async () => {
    seedReady({ nodes: [] });
    render(<CanvasView canvasId="c1" />);
    await screen.findByTestId('canvas-surface');
    expect(screen.queryByText('canvas.backToList')).toBeNull();
  });

  it('route mode (onBack provided) renders the back pill', async () => {
    seedReady({ nodes: [] });
    render(<CanvasView canvasId="c1" onBack={() => {}} />);
    expect(await screen.findByText('canvas.backToList')).toBeInTheDocument();
  });

  it('the default route export (CanvasPage) still renders the back pill via its own history-based onBack', async () => {
    seedReady({ nodes: [] });
    render(<CanvasPage />);
    expect(await screen.findByText('canvas.backToList')).toBeInTheDocument();
  });
});
