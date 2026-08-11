/**
 * CanvasPage's Task 4 shot-node reconcile + promote-to-shot wiring.
 *
 * `reconcileShotNodes` itself is exhaustively unit-tested in
 * `../smart/shotSync.test.ts` (pure logic, no mocks needed). This file
 * covers the impure glue this page adds around it: does the mount effect
 * actually fetch scenes/shots and apply the diff to the store, does it stay
 * inert for non-storyboard canvases, and does the promote dialog create a
 * shot and patch the requesting node.
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

vi.mock('./CanvasSurface', () => ({
  CanvasSurface: () => <div data-testid="canvas-surface" />,
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

// Final review Critical 1 regression coverage: CanvasPage's mount effect
// must call `resumePendingGenerations` for kind==='storyboard' even though
// 'storyboard' is deliberately excluded from `isSmartFamily` (T4/T5 —
// composer/Arrange must not appear on the storyboard canvas). Mocked wholesale
// rather than spied-through-actual: none of this file's other tests seed a
// generating prompt/shot node, so the real implementation would no-op for
// them regardless — this keeps the assertion focused on "was it called at
// all" without coupling to genResume's own (separately unit-tested) internals.
const resumePendingGenerations = vi.fn().mockResolvedValue(undefined);
vi.mock('../smart/genResume', () => ({
  resumePendingGenerations: (...args: unknown[]) => resumePendingGenerations(...args),
}));

import CanvasPage, { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { requestPromoteShot } from '../smart/promoteShotBus';

const SCENE_1 = {
  id: 'scene-1',
  script_id: 's1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'House',
  time_of_day: 'DAY',
  content_version: 1,
  elements: [],
  sort_order: 1,
};

const SHOT_1 = {
  id: 'shot-db-1',
  scene_id: 'scene-1',
  shot_number: 1,
  shot_type: 'MEDIUM',
  camera_angle: 'EYE_LEVEL',
  camera_movement: 'STATIC',
  focal_length: '35mm',
  lighting: null,
  description: 'Dolly in',
  image_url: null,
  thumbnail_url: null,
  video_url: null,
  status: 'empty',
  sort_order: 1000,
};

const seedReady = (
  overrides: Partial<{ kind: string; projectId: string | null; episodeId: string | null; nodes: unknown[] }> = {},
) => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    // `sameCanvas()` guards inside the reconcile effect compare this
    // against the route's `canvasId` ('c1', from the mocked useParams) —
    // must match or every post-await patch is dropped as "stale".
    canvasId: 'c1',
    kind: overrides.kind ?? 'storyboard',
    projectId: overrides.projectId ?? 'proj-1',
    episodeId: overrides.episodeId ?? 'ep-1',
    nodes: (overrides.nodes ?? []) as never,
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  } as never);
};

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  fetchScriptProjects.mockReset();
  listScenes.mockReset();
  listShots.mockReset();
  createShot.mockReset();
  resumePendingGenerations.mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CanvasPage — Task 4 shot reconcile wiring', () => {
  it('storyboard canvas: resolves the script, fetches scenes/shots, and onboards a new shot node', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([SCENE_1]);
    listShots.mockResolvedValue([SHOT_1]);

    seedReady();
    render(<CanvasPage />);

    await waitFor(() => {
      expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
    });
    const node = useCanvasCoreStore.getState().nodes[0] as { id: string; data: { shot_id: string } };
    expect(node.id).toBe('shot-shot-db-1');
    expect(node.data.shot_id).toBe('shot-db-1');
    expect(listScenes).toHaveBeenCalledWith('script-1');
    expect(listShots).toHaveBeenCalledWith('scene-1');
  });

  it('non-storyboard canvas: never calls the script/scene services', async () => {
    seedReady({ kind: 'smart' });
    render(<CanvasPage />);
    // Give any stray microtask a chance to run before asserting the negative.
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchScriptProjects).not.toHaveBeenCalled();
    expect(listScenes).not.toHaveBeenCalled();
  });

  it('no script yet for this episode: reconcile is a no-op (never provisions one)', async () => {
    fetchScriptProjects.mockResolvedValue({ data: [] });
    seedReady();
    render(<CanvasPage />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(listScenes).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });

  // Review fix round 1 — regression coverage for the fetch-failure cascade
  // guard: the whole fetch chain is inside ONE try/catch that aborts BEFORE
  // any store write (`reconcileShotNodes` is only called after every await
  // resolves), so a mid-chain rejection must never partially apply a diff.
  // The code path was already correct; this was flagged as a data-accident-
  // class gap with no test pinning it.
  it('listScenes rejecting aborts before listShots runs or any node is touched', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockRejectedValue(new Error('network down'));
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const existingNodes = [
      {
        id: 'draft-1',
        type: 'shot',
        position: { x: 0, y: 0 },
        data: { title: 'Draft', reference_resource_ids: [], notes: '', shot_id: null },
      },
    ];
    seedReady({ nodes: existingNodes });
    render(<CanvasPage />);

    await waitFor(() => expect(listScenes).toHaveBeenCalled());
    // Let the rejection's catch handler run.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(listShots).not.toHaveBeenCalled();
    expect(createShot).not.toHaveBeenCalled();
    // The pre-existing node set is byte-for-byte untouched — no
    // appendElementsNoHistory / patchNode fired (those are the only ops
    // that could change `nodes`, so an unchanged array proves neither ran).
    expect(useCanvasCoreStore.getState().nodes).toEqual(existingNodes);
    consoleErrorSpy.mockRestore();
  });

  it('listShots rejecting (one scene) aborts before any node is added/patched/marked stale', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([SCENE_1]);
    listShots.mockRejectedValue(new Error('network down'));
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    seedReady();
    render(<CanvasPage />);

    await waitFor(() => expect(listShots).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
    consoleErrorSpy.mockRestore();
  });

  it('promote-to-shot: picking a scene creates the shot and patches the requesting node', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([SCENE_1]);
    // First call (reconcile) sees no existing shots; second call (post-create
    // re-index for the label) sees the freshly created one.
    listShots.mockResolvedValueOnce([]).mockResolvedValueOnce([{ ...SHOT_1, id: 'new-shot-1' }]);
    createShot.mockResolvedValue({ ...SHOT_1, id: 'new-shot-1' });

    seedReady({
      nodes: [
        {
          id: 'draft-1',
          type: 'shot',
          position: { x: 0, y: 0 },
          data: { title: 'Draft', reference_resource_ids: [], notes: '', shot_id: null },
        },
      ],
    });
    render(<CanvasPage />);

    // Let the reconcile pass resolve first (populates the scene list the
    // dialog needs) before firing the promote request.
    await waitFor(() => expect(listScenes).toHaveBeenCalled());

    act(() => {
      requestPromoteShot('draft-1');
    });

    expect(await screen.findByTestId('promote-shot-dialog')).toBeTruthy();
    const option = await screen.findByTestId('promote-shot-scene-option');
    act(() => {
      option.click();
    });

    await waitFor(() => {
      expect(createShot).toHaveBeenCalledWith('scene-1', {});
    });
    await waitFor(() => {
      const node = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as { id: string }).id === 'draft-1') as {
        data: { shot_id: string | null; shot_label: string | null };
      };
      expect(node.data.shot_id).toBe('new-shot-1');
      expect(node.data.shot_label).toBe('1A');
    });
    expect(screen.queryByTestId('promote-shot-dialog')).toBeNull();
  });

  // Task 6 review 修复轮1 (2026-08-11): an Agent Run Undo can mutate
  // script_shots while this canvas is ALREADY mounted (unlike every test
  // above, which only covers the mount-time reconcile) — `EpisodeStoryboardPage`
  // bumps `reconcileRefreshToken` when that happens (see its own
  // `onStoryboardRefresh` subscription). Only reachable through `CanvasView`
  // directly (the embedded prop, not the standalone route) — same reason
  // `CanvasPage.focus.test.tsx` imports it for `focusShotId`.
  it('reconcileRefreshToken bump re-runs the SAME reconcile fetch (Agent Run Undo while canvas mounted)', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([SCENE_1]);
    listShots.mockResolvedValue([SHOT_1]);

    seedReady();
    const { rerender } = render(<CanvasView canvasId="c1" reconcileRefreshToken={0} />);

    await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(1));
    expect(listShots).toHaveBeenCalledTimes(1);

    rerender(<CanvasView canvasId="c1" reconcileRefreshToken={1} />);

    await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(2));
    expect(listShots).toHaveBeenCalledTimes(2);
    // Same call shape both times — this is the existing reconcile effect
    // re-running verbatim, not a different code path.
    expect(listScenes).toHaveBeenNthCalledWith(2, 'script-1');
    expect(listShots).toHaveBeenNthCalledWith(2, 'scene-1');
  });

  it('an unrelated re-render (reconcileRefreshToken unchanged) does not re-run the reconcile fetch', async () => {
    fetchScriptProjects.mockResolvedValue({
      data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
    });
    listScenes.mockResolvedValue([SCENE_1]);
    listShots.mockResolvedValue([SHOT_1]);

    seedReady();
    const { rerender } = render(<CanvasView canvasId="c1" reconcileRefreshToken={0} />);
    await waitFor(() => expect(listScenes).toHaveBeenCalledTimes(1));

    rerender(<CanvasView canvasId="c1" reconcileRefreshToken={0} teamId="t2" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(listScenes).toHaveBeenCalledTimes(1);
  });
});

describe('CanvasPage — resumePendingGenerations wiring (final review Critical 1)', () => {
  it('storyboard kind: mounting still triggers the resume sweep despite isSmartFamily excluding it', async () => {
    fetchScriptProjects.mockResolvedValue({ data: [] });
    seedReady({ kind: 'storyboard' });
    render(<CanvasPage />);

    await waitFor(() => {
      expect(resumePendingGenerations).toHaveBeenCalled();
    });
  });

  it('smart kind: still triggers the resume sweep (isSmartFamily path unaffected by the fix)', async () => {
    seedReady({ kind: 'smart' });
    render(<CanvasPage />);

    await waitFor(() => {
      expect(resumePendingGenerations).toHaveBeenCalled();
    });
  });

  it('does not fire before loadStatus is ready', async () => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      loadStatus: 'loading',
      canvasId: 'c1',
      kind: 'storyboard',
      projectId: 'proj-1',
      episodeId: 'ep-1',
      nodes: [] as never,
      loadCanvas: vi.fn(),
      flushSave: vi.fn().mockResolvedValue(undefined),
      reset: vi.fn(),
    } as never);
    render(<CanvasPage />);

    await act(async () => {
      await Promise.resolve();
    });
    expect(resumePendingGenerations).not.toHaveBeenCalled();
  });
});
