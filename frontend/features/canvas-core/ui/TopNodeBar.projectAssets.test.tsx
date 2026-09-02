// features/canvas-core/ui/TopNodeBar.projectAssets.test.tsx
//
// "Project Assets" — the bulk sibling of the Asset chip (P4 Task 6): pull the
// whole project shelf onto the board in one press, one lane per type.
//
// The rows here are `AssetRow`s as `GET /projects/{id}/assets` really sends
// them: every BIGINT column is a JSON string, and `file_counts_by_slot` is a
// tally rather than the file rows — which is exactly why a bulk-inserted card
// starts with an empty selection.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AssetRow, AssetType } from '../../../services/assetsService';

const SCOPE = '727145299382534100';
const PROJECT = '337610660408111';

const listProjectAssets = vi.fn();
vi.mock('../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    listProjectAssets: (...a: unknown[]) => listProjectAssets(...a),
    searchAssets: vi.fn().mockResolvedValue([]),
    fetchAssetDetail: vi.fn(),
  };
});

// `t` is stubbed with the interpolation the real i18next would do, because
// the counts ARE the assertion: a toast that renders "Added {{inserted}}"
// would pass a check on the raw key while telling the user nothing.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, second?: unknown, third?: unknown) => {
      const opts = (typeof second === 'object' ? second : third) as
        | Record<string, unknown>
        | undefined;
      const fallback =
        typeof second === 'string'
          ? second
          : ((opts?.defaultValue as string | undefined) ?? key);
      return fallback.replace(/\{\{(\w+)\}\}/g, (_m, name: string) =>
        String(opts?.[name] ?? ''),
      );
    },
  }),
}));

const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast }),
  useToast: () => ({ addToast }),
}));

import { TopNodeBar } from './TopNodeBar';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

function row(id: string, type: AssetType, name: string): AssetRow {
  return {
    id,
    scope_id: SCOPE,
    asset_type: type,
    subtype: null,
    name,
    role_tag: '',
    description: '',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    tags: {},
    sort_order: 0,
    created_by: null,
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: { sheet: 2 },
    project_ids: [PROJECT],
    loadout_count: 0,
  };
}

const FOUR = [
  row('11', 'character', 'Cole Bannon'),
  row('22', 'location', 'Dock'),
  row('33', 'prop', 'Lantern'),
  row('44', 'costume', 'Oilskin'),
];

const surfaceRef = {
  current: {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
  } as unknown as HTMLDivElement,
};

function renderBar() {
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/9`]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={<TopNodeBar surfaceRef={surfaceRef} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

const press = () => fireEvent.click(screen.getByTestId('top-node-chip-project-assets'));

beforeEach(() => {
  listProjectAssets.mockReset().mockResolvedValue(FOUR);
  addToast.mockReset();
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    kind: 'smart',
    loadStatus: 'ready',
    projectId: PROJECT,
    viewport: { x: 0, y: 0, zoom: 1 },
  } as never);
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('Project Assets', () => {
  it('lays the four kinds out in four lanes and selects what it added', async () => {
    renderBar();
    press();
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(4));
    expect(listProjectAssets).toHaveBeenCalledWith(PROJECT);

    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      type: string;
      position: { x: number; y: number };
      data: { asset_id: string };
    }>;
    expect(nodes.every((n) => n.type === 'asset')).toBe(true);
    // Four distinct columns, one row deep each.
    const xs = [...new Set(nodes.map((n) => n.position.x))];
    expect(xs).toHaveLength(4);
    expect(new Set(nodes.map((n) => n.position.y))).toEqual(new Set([0]));
    // Lane order is the asset-type order, not the order the server listed.
    expect(nodes.map((n) => n.data.asset_id)).toEqual(['11', '22', '33', '44']);
    expect(useCanvasCoreStore.getState().selection).toEqual(nodes.map((n) => n.id));
  });

  it('stacks several assets of one kind down that kind lane', async () => {
    listProjectAssets.mockResolvedValue([
      row('11', 'character', 'Cole'),
      row('12', 'character', 'Mara'),
      row('22', 'location', 'Dock'),
    ]);
    renderBar();
    press();
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(3));
    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      position: { x: number; y: number };
      data: { asset_id: string };
    }>;
    const at = (id: string) => nodes.find((n) => n.data.asset_id === id)!.position;
    expect(at('11').x).toBe(at('12').x);
    expect(at('12').y).toBeGreaterThan(at('11').y);
    expect(at('22').x).toBeGreaterThan(at('11').x);
  });

  it('skips an asset already on the canvas and says how many', async () => {
    useCanvasCoreStore.setState({
      nodes: [
        {
          id: 'asset-0-aaaa',
          type: 'asset',
          position: { x: 0, y: 0 },
          data: { asset_id: '11' },
        },
      ],
    } as never);
    renderBar();
    press();
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(4));
    const ids = (
      useCanvasCoreStore.getState().nodes as Array<{ data: { asset_id: string } }>
    ).map((n) => n.data.asset_id);
    // Three added, none duplicated.
    expect(ids).toEqual(['11', '22', '33', '44']);
    expect(addToast).toHaveBeenCalledWith('Added 3 · 1 already here', 'success');
  });

  it('creates NOTHING and says so when the project has no assets', async () => {
    listProjectAssets.mockResolvedValue([]);
    renderBar();
    press();
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
    expect(addToast).toHaveBeenCalledWith('This project has no assets yet', 'info');
  });

  it('creates NOTHING and says so when everything is already here', async () => {
    useCanvasCoreStore.setState({
      nodes: FOUR.map((asset, i) => ({
        id: `asset-${i}`,
        type: 'asset',
        position: { x: 0, y: 0 },
        data: { asset_id: asset.id },
      })),
    } as never);
    renderBar();
    press();
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(4);
    expect(addToast).toHaveBeenCalledWith(
      'Every project asset is already here',
      'info',
    );
  });

  it('reports a failed request rather than looking like an empty project', async () => {
    listProjectAssets.mockRejectedValue(new Error('500'));
    renderBar();
    press();
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(addToast).toHaveBeenCalledWith('Could not load the project assets', 'error');
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });

  it('refuses, out loud, when the canvas row carries no project', async () => {
    useCanvasCoreStore.setState({ projectId: null } as never);
    renderBar();
    press();
    expect(listProjectAssets).not.toHaveBeenCalled();
    expect(addToast).toHaveBeenCalledWith('This canvas has no project', 'error');
  });
});
