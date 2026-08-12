/**
 * 2026-08-12 production incident — full mount-cycle regression test.
 *
 * Production shape, end to end: the REAL singleton store `loadCanvas`/
 * `flushSave`/`reset` actions run against a fake server row (unlike
 * `CanvasPage.shotSync.test.tsx`, which seeds `loadStatus: 'ready'` and
 * stubs those actions — exactly the gap that let the incident ship: no test
 * ever exercised load → reconcile → save → reload with the wire shapes
 * production actually produces).
 *
 * The two wire shapes that mattered:
 *  - `listShots` rows carry bigint ids as JSON *numbers* (the shots REST
 *    router doesn't stringify; the `Shot` interface only claimed strings).
 *  - The canvas row round-trips `nodes_json` through the save endpoint, so
 *    whatever reconcile appends is what the next mount loads.
 *
 * With the shipped code, every storyboard-tab visit (T7 keep-alive unmounts
 * and remounts the embed per tab toggle) re-added the entire shot set:
 * numeric ids never matched the `typeof === 'string'` bound-node index, the
 * row grew by N nodes per mount (102 nodes for 6 shots in production), and
 * React Flow renders duplicated ids permanently `visibility:hidden` — the
 * "blank canvas" symptom. This test mounts the same canvas 3 times and
 * pins: the node count NEVER grows.
 */

import React from 'react';
import { render, waitFor, cleanup } from '@testing-library/react';
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
vi.mock('../smart/genResume', () => ({
  resumePendingGenerations: vi.fn().mockResolvedValue(undefined),
}));

// ---- Fake server row (the singleton store's loadImpl/saveImpl resolve to
// these mocks because vi.mock rewires the module graph before the store
// module binds them). ------------------------------------------------------
interface FakeRow {
  [k: string]: unknown;
  nodes_json: Array<Record<string, unknown>>;
  base_updated_at: string;
}
let serverRow: FakeRow;
const getCanvas = vi.fn(async () => ({ ...serverRow }));
const saveCanvas = vi.fn(
  async (_id: string, payload: { base_updated_at: string; nodes_json?: unknown }) => {
    if (payload.base_updated_at !== serverRow.base_updated_at) {
      return { ok: false as const, conflict: { ...serverRow } };
    }
    const nextTs = new Date(
      new Date(serverRow.base_updated_at).getTime() + 1000,
    ).toISOString();
    serverRow = {
      ...serverRow,
      ...payload,
      base_updated_at: nextTs,
      updated_at: nextTs,
    } as FakeRow;
    return { ok: true as const, canvas: { ...serverRow } };
  },
);
vi.mock('../services/canvasService', () => ({
  getCanvas: (...args: unknown[]) => (getCanvas as (...a: unknown[]) => unknown)(...args),
  saveCanvas: (...args: unknown[]) => (saveCanvas as (...a: unknown[]) => unknown)(...args),
}));

const fetchScriptProjects = vi.fn();
vi.mock('../../../services/scriptService', () => ({
  fetchScriptProjects: (...args: unknown[]) => fetchScriptProjects(...args),
}));

const listScenes = vi.fn();
const listShots = vi.fn();
vi.mock('../../../editor/sceneService', () => ({
  listScenes: (...args: unknown[]) => listScenes(...args),
  listShots: (...args: unknown[]) => listShots(...args),
  createShot: vi.fn(),
}));

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

// Production wire shape: bigint ids as JSON numbers (53-bit snowflakes —
// exact in a JS number, wrong TYPE). scene docs go through toSceneDoc's
// String() coercion in the real service, so scenes are strings; shots are
// the raw numeric rows the incident actually fed reconcile.
const SCENE_ID = 208443000000100;
const SHOT_IDS = [208443000000001, 208443000000002];
const numericShot = (id: number) => ({
  id,
  scene_id: SCENE_ID,
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
});

beforeEach(() => {
  serverRow = {
    id: 'c1',
    project_id: 'proj-1',
    episode_id: 'ep-1',
    name: 'EP1 · Storyboard',
    kind: 'storyboard',
    viewport_json: { x: 0, y: 0, zoom: 1 },
    nodes_json: [],
    connections_json: [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: '2026-06-10T12:00:00+00:00',
    created_at: '2026-06-10T12:00:00+00:00',
    updated_at: '2026-06-10T12:00:00+00:00',
    created_by: null,
  };
  getCanvas.mockClear();
  saveCanvas.mockClear();
  fetchScriptProjects.mockReset().mockResolvedValue({
    data: [{ id: 'script-1', episode_id: 'ep-1', updated_at: '2026-01-01T00:00:00Z' }],
  });
  listScenes.mockReset().mockResolvedValue([
    {
      id: String(SCENE_ID),
      script_id: 'script-1',
      chapter_id: null,
      heading_int_ext: 'INT',
      location_text: 'House',
      time_of_day: 'DAY',
      content_version: 1,
      elements: [],
      sort_order: 1,
    },
  ]);
  listShots.mockReset().mockResolvedValue(SHOT_IDS.map(numericShot));
});

afterEach(async () => {
  cleanup();
  // Let any trailing unmount flushSave settle before the next test.
  await Promise.resolve();
  useCanvasCoreStore.getState().reset();
  vi.clearAllMocks();
});

async function mountOnce(): Promise<() => void> {
  const { unmount } = render(<CanvasView canvasId="c1" />);
  await waitFor(() => {
    expect(useCanvasCoreStore.getState().loadStatus).toBe('ready');
  });
  // Reconcile settles: both shots present as nodes, exactly once each.
  await waitFor(() => {
    expect(useCanvasCoreStore.getState().nodes.length).toBeGreaterThanOrEqual(2);
  });
  return unmount;
}

describe('storyboard canvas mount→unmount→mount ×3 (production shapes)', () => {
  it('node count never grows across remounts; the persisted row stays at one node per shot', async () => {
    for (let cycle = 1; cycle <= 3; cycle++) {
      const unmount = await mountOnce();

      const ids = (useCanvasCoreStore.getState().nodes as Array<{ id: string }>).map(
        (n) => n.id,
      );
      // THE incident assertion: one node per shot, every cycle. The shipped
      // code failed this on cycle 2 (4 nodes) and cycle 3 (6 nodes).
      expect(ids.sort()).toEqual([
        'shot-208443000000001',
        'shot-208443000000002',
      ]);

      unmount();
      // The unmount cleanup fire-and-forgets flushSave(); wait for the
      // persisted row to settle before the next cycle loads it back.
      await waitFor(() => {
        expect(saveCanvas).toHaveBeenCalled();
      });
      await waitFor(() => {
        expect(useCanvasCoreStore.getState().loadStatus).toBe('idle');
      });
    }

    // The DB row itself never accumulated duplicates.
    const persistedIds = serverRow.nodes_json.map((n) => n.id);
    expect(persistedIds.sort()).toEqual([
      'shot-208443000000001',
      'shot-208443000000002',
    ]);
    // And the healed row carries canonical STRING ids in node data.
    for (const n of serverRow.nodes_json) {
      const data = n.data as { shot_id?: unknown; scene_id?: unknown };
      expect(typeof data.shot_id).toBe('string');
      expect(typeof data.scene_id).toBe('string');
    }
  });
});
