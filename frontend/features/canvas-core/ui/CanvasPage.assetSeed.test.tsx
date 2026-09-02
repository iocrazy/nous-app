/**
 * `CanvasView`'s two asset-library load hooks (P4 Task 6):
 *
 *   1. an EMPTY canvas that BELONGS to an asset seeds one card for it;
 *   2. legacy `character`/`location`/`prop` cards are resolved against
 *      `GET /assets/resolve-legacy` and rewritten in place.
 *
 * Both live here because they share a harness and a failure mode: neither may
 * block the first paint, and neither may write anything it did not verify.
 *
 * Mocks copied from `CanvasPage.nodeParam.test.tsx`, with `useParams` also
 * answering a `teamId` — that URL segment IS the asset scope (`canvasScope.ts`),
 * so without it the hooks below correctly ask nothing at all.
 */

import React from 'react';
import { act, render, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';

let params: Record<string, string | undefined> = { canvasId: 'c1', teamId: SCOPE };
vi.mock('react-router-dom', () => ({
  useParams: () => params,
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams()],
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast }),
  useToast: () => ({ addToast }),
}));

const fetchAssetDetail = vi.fn();
const resolveLegacyAsset = vi.fn();
vi.mock('../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
    resolveLegacyAsset: (...a: unknown[]) => resolveLegacyAsset(...a),
  };
});

vi.mock('./CanvasSurface', () => ({
  CanvasSurface: ({ onInit }: { onInit?: (instance: unknown) => void }) => {
    React.useEffect(() => {
      onInit?.({ fitView: vi.fn() });
    }, [onInit]);
    return <div data-testid="canvas-surface" />;
  },
}));
vi.mock('../smart/CanvasComposer', () => ({ CanvasComposer: () => null }));
vi.mock('../palette/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('./CanvasConflictDialog', () => ({ CanvasConflictDialog: () => null }));
vi.mock('./TopNodeBar', () => ({ TopNodeBar: () => null }));
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

import { CanvasView } from './CanvasPage';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const assetDetail = (over: Record<string, unknown> = {}) => ({
  id: ASSET_ID,
  scope_id: SCOPE,
  asset_type: 'character',
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
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
  file_counts_by_slot: {},
  project_ids: [],
  loadout_count: 0,
  files: [],
  links: [],
  linked_by: [],
  loadouts: [],
  ...over,
});

const legacyCharacter = (over: Record<string, unknown> = {}) => ({
  id: 'character-1-abcd',
  type: 'character',
  position: { x: 40, y: 80 },
  data: {
    character_id: '400000000000000001',
    name: 'Cole',
    role_tag: 'lead',
    description: '',
    portrait_url: null,
  },
  ...over,
});

function seedReady(over: Record<string, unknown>) {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    loadStatus: 'ready',
    canvasId: 'c1',
    kind: 'smart',
    projectId: 'proj-1',
    nodes: [],
    connections: [],
    loadCanvas: vi.fn(),
    flushSave: vi.fn().mockResolvedValue(undefined),
    ...over,
  } as never);
}

const renderView = () => render(<CanvasView canvasId="c1" teamId={SCOPE} />);

beforeEach(() => {
  params = { canvasId: 'c1', teamId: SCOPE };
  fetchAssetDetail.mockReset().mockResolvedValue(assetDetail());
  resolveLegacyAsset.mockReset().mockResolvedValue(null);
  addToast.mockReset();
  useCanvasCoreStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('asset-bound seeding', () => {
  it('seeds ONE card bound to the canvas asset', async () => {
    seedReady({ assetId: ASSET_ID });
    renderView();
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(1));

    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET_ID);
    const node = useCanvasCoreStore.getState().nodes[0] as {
      type: string;
      data: { asset_id: string };
    };
    expect(node.type).toBe('asset');
    expect(node.data.asset_id).toBe(ASSET_ID);
  });

  it('seeds ONCE per canvas, even across re-renders', async () => {
    seedReady({ assetId: ASSET_ID });
    const { rerender } = renderView();
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(1));
    await act(async () => {
      rerender(<CanvasView canvasId="c1" teamId={SCOPE} />);
    });
    expect(fetchAssetDetail).toHaveBeenCalledTimes(1);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('never touches a canvas that already has work on it', async () => {
    seedReady({
      assetId: ASSET_ID,
      nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: {} }],
    });
    renderView();
    await act(async () => {});
    expect(fetchAssetDetail).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('does nothing for a canvas that belongs to no asset', async () => {
    seedReady({ assetId: null });
    renderView();
    await act(async () => {});
    expect(fetchAssetDetail).not.toHaveBeenCalled();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });

  it('asks nothing when the route carries no scope', async () => {
    params = { canvasId: 'c1' };
    seedReady({ assetId: ASSET_ID });
    renderView();
    await act(async () => {});
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });

  it('says so when the asset cannot be read, instead of a silent blank board', async () => {
    fetchAssetDetail.mockRejectedValue(new Error('500'));
    seedReady({ assetId: ASSET_ID });
    renderView();
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(addToast).toHaveBeenCalledWith('Could not load this canvas asset', 'error');
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });
});

describe('legacy card migration', () => {
  it('renders the legacy card FIRST, then swaps it for an asset card', async () => {
    resolveLegacyAsset.mockResolvedValue(ASSET_ID);
    seedReady({ nodes: [legacyCharacter()] });
    renderView();

    // Nothing has been asked yet at the moment of the first paint — the card
    // on screen is still the legacy one.
    expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
      'character',
    );

    await waitFor(() =>
      expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
        'asset',
      ),
    );
    expect(resolveLegacyAsset).toHaveBeenCalledWith(
      SCOPE,
      'character',
      '400000000000000001',
    );
    const node = useCanvasCoreStore.getState().nodes[0] as {
      id: string;
      position: unknown;
      data: { asset_id: string };
    };
    expect(node.id).toBe('character-1-abcd');
    expect(node.position).toEqual({ x: 40, y: 80 });
    expect(node.data.asset_id).toBe(ASSET_ID);
  });

  it('a miss keeps the card and marks it Unmigrated', async () => {
    resolveLegacyAsset.mockResolvedValue(null);
    seedReady({ nodes: [legacyCharacter()] });
    renderView();
    await waitFor(() =>
      expect(
        (useCanvasCoreStore.getState().nodes[0] as { data: { unmigrated?: boolean } })
          .data.unmigrated,
      ).toBe(true),
    );
    expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
      'character',
    );
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });

  it('a failed lookup changes nothing — "could not ask" is not "no asset"', async () => {
    resolveLegacyAsset.mockRejectedValue(new Error('offline'));
    seedReady({ nodes: [legacyCharacter()] });
    renderView();
    await waitFor(() => expect(resolveLegacyAsset).toHaveBeenCalled());
    await act(async () => {});
    const node = useCanvasCoreStore.getState().nodes[0] as {
      type: string;
      data: { unmigrated?: boolean };
    };
    expect(node.type).toBe('character');
    expect(node.data.unmigrated).toBeUndefined();
  });

  it('does NOT force a save — the rewrite rides out with the next real edit', async () => {
    resolveLegacyAsset.mockResolvedValue(ASSET_ID);
    seedReady({ nodes: [legacyCharacter()] });
    renderView();
    await waitFor(() =>
      expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
        'asset',
      ),
    );
    // `revision` is what `markDirty` bumps and what schedules the debounced
    // PUT. Opening an old board must not be a write.
    expect(useCanvasCoreStore.getState().revision).toBe(0);
  });

  it('leaves every connection valid, because the node id did not move', async () => {
    resolveLegacyAsset.mockResolvedValue(ASSET_ID);
    seedReady({
      nodes: [
        legacyCharacter(),
        { id: 'prompt-2', type: 'prompt', position: { x: 400, y: 0 }, data: {} },
      ],
      connections: [{ id: 'e1', source: 'character-1-abcd', target: 'prompt-2' }],
    });
    renderView();
    await waitFor(() =>
      expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
        'asset',
      ),
    );
    const state = useCanvasCoreStore.getState();
    const ids = new Set(state.nodes.map((n) => (n as { id: string }).id));
    const edge = state.connections[0] as { source: string; target: string };
    expect(ids.has(edge.source)).toBe(true);
    expect(ids.has(edge.target)).toBe(true);
  });

  it('applies to the LIVE list — a drag during the round trip is not undone', async () => {
    // The verdicts are computed from the snapshot taken at load; writing that
    // snapshot back would silently revert whatever the user did while the
    // requests were in flight. Pinned at the PAGE level because the choice of
    // which list to hand `applyLegacyVerdicts` is made here.
    let release: (id: string) => void = () => {};
    resolveLegacyAsset.mockImplementation(
      () => new Promise<string>((resolve) => {
        release = resolve;
      }),
    );
    seedReady({ nodes: [legacyCharacter()] });
    renderView();
    await waitFor(() => expect(resolveLegacyAsset).toHaveBeenCalled());

    // The user drags the card while the lookup is out.
    act(() => {
      useCanvasCoreStore.getState().setNodes([
        legacyCharacter({ position: { x: 999, y: 777 } }),
      ]);
    });

    await act(async () => {
      release(ASSET_ID);
    });

    await waitFor(() =>
      expect((useCanvasCoreStore.getState().nodes[0] as { type: string }).type).toBe(
        'asset',
      ),
    );
    expect((useCanvasCoreStore.getState().nodes[0] as { position: unknown }).position)
      .toEqual({ x: 999, y: 777 });
  });

  it('asks nothing when there is no legacy card to resolve', async () => {
    seedReady({
      nodes: [{ id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: '9' } }],
    });
    renderView();
    await act(async () => {});
    expect(resolveLegacyAsset).not.toHaveBeenCalled();
  });
});
