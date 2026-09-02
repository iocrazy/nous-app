// features/canvas-core/smart/nodes/AssetNodeView.test.tsx
//
// The asset reference card (P4 Task 4).
//
// Fixtures are WIRE-SHAPED: `fetchAssetDetail` is a service function whose
// documented return type stringifies every BIGINT (`AssetRowDetail`), so its
// ids are strings here — the `frontend/e2e/helpers/realShapes.ts` distinction
// between "an HTTP body" and "a normalized service return" (CLAUDE.md).
//
// The load branch that matters most is the negative one: only a 404 may set
// `removed`. A network failure answering the same way would write a permanent
// "this asset was deleted" claim into `nodes_json` about something nobody
// actually checked.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { GeneratedApiError, NETWORK_STATUS } from '../../../../services/apiEnvelope';
import type { AssetRowDetail } from '../../../../services/assetsService';
import { AssetNodeView, orderedReferenceFiles } from './AssetNodeView';

const fetchAssetDetail = vi.fn();

vi.mock('../../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchAssetDetail: (...args: unknown[]) => fetchAssetDetail(...args),
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === 'string'
        ? fallback
        : typeof fallback === 'object' && fallback !== null
          ? String((fallback as { defaultValue?: string }).defaultValue ?? key)
          : key,
  }),
}));

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';
const LOADOUT_A = '727145299382534401';
const LOADOUT_B = '727145299382534402';

const SHEET_FILE = '900000000000000001';
const STILLS_FILE = '900000000000000002';
const WORN_A_FILE = '900000000000000003';
const WORN_B_FILE = '900000000000000004';

function file(over: Partial<AssetRowDetail['files'][number]>) {
  return {
    asset_id: ASSET_ID,
    resource_id: SHEET_FILE,
    slot: 'sheet',
    loadout_id: null,
    sort_order: 0,
    note: null,
    attached_by: null,
    attached_at: '2026-09-02T00:00:00+00:00',
    ...over,
  };
}

const DETAIL: AssetRowDetail = {
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
  cover_file_id: SHEET_FILE,
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-01T00:00:00+00:00',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1, stills: 1, worn: 2 },
  project_ids: [],
  loadout_count: 2,
  files: [
    file({ resource_id: STILLS_FILE, slot: 'stills', sort_order: 1 }),
    file({ resource_id: SHEET_FILE, slot: 'sheet', sort_order: 0 }),
    file({ resource_id: WORN_A_FILE, slot: 'worn', loadout_id: LOADOUT_A }),
    file({ resource_id: WORN_B_FILE, slot: 'worn', loadout_id: LOADOUT_B }),
  ],
  links: [],
  linked_by: [],
  loadouts: [
    {
      id: LOADOUT_A,
      asset_id: ASSET_ID,
      name: 'Field Coat',
      is_default: true,
      costume_ids: [],
      prop_ids: [],
      prompt_extra: null,
      sort_order: 0,
      created_at: '2026-09-01T00:00:00+00:00',
    },
    {
      id: LOADOUT_B,
      asset_id: ASSET_ID,
      name: 'Dress Blues',
      is_default: false,
      costume_ids: [],
      prop_ids: [],
      prompt_extra: null,
      sort_order: 1,
      created_at: '2026-09-01T00:00:00+00:00',
    },
  ],
};

const NODE_DATA = {
  asset_id: ASSET_ID,
  loadout_id: null as string | null,
  selected_file_ids: [SHEET_FILE],
  name: 'Cole Bannon',
  asset_type: 'character' as const,
  cover_file_id: SHEET_FILE,
  readiness_state: 'ready' as const,
};

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

function seedAndRender(
  data: Record<string, unknown> = NODE_DATA,
  opts: { readOnly?: boolean; teamId?: string | null } = {},
) {
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    readOnly: opts.readOnly ?? false,
    nodes: [{ id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  });
  const teamId = opts.teamId === undefined ? SCOPE : opts.teamId;
  const path = teamId ? `/team/${teamId}/canvas/9` : '/canvas/9';
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path={teamId ? '/team/:teamId/canvas/:canvasId' : '/canvas/:canvasId'}
          element={
            <ReactFlowProvider>
              <AssetNodeView {...baseProps} id="a1" type="asset" data={data} />
            </ReactFlowProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

function nodeData(): Record<string, unknown> {
  const n = useCanvasCoreStore.getState().nodes[0] as Record<string, any>;
  return n.data;
}

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  fetchAssetDetail.mockReset();
  fetchAssetDetail.mockResolvedValue(DETAIL);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  vi.restoreAllMocks();
});

describe('AssetNodeView — snapshot render', () => {
  it('renders the snapshot before the detail fetch resolves', () => {
    // A pending promise: the card must be useful with nothing loaded.
    fetchAssetDetail.mockReturnValue(new Promise(() => {}));
    seedAndRender();
    expect(screen.getByTestId('smart-asset-node')).toHaveAttribute(
      'data-asset-id',
      ASSET_ID,
    );
    expect(screen.getByText('Cole Bannon')).toBeTruthy();
    // The i18n mock answers with the raw fallback; the real locale carries
    // `saveAsAsset.type.character` = "Character" (pinned by the asset
    // library's own i18nParity test).
    expect(screen.getByTestId('asset-node-type-chip')).toHaveTextContent('character');
    expect(screen.getByTestId('asset-node-readiness')).toHaveAttribute(
      'data-readiness',
      'ready',
    );
  });

  it('asks the assets API with the team segment from the URL', async () => {
    seedAndRender();
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET_ID));
  });

  it('does not ask at all when the route carries no team segment', () => {
    seedAndRender(NODE_DATA, { teamId: null });
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });

  it('falls back to the type icon when there is no cover', () => {
    fetchAssetDetail.mockReturnValue(new Promise(() => {}));
    seedAndRender({ ...NODE_DATA, cover_file_id: null });
    expect(screen.queryByTestId('asset-node-cover')).toBeNull();
    expect(screen.getByTestId('asset-node-cover-fallback')).toBeTruthy();
  });

  it('links to the asset sheet under the same team base path', () => {
    seedAndRender();
    expect(screen.getByTestId('asset-node-open-sheet')).toHaveAttribute(
      'href',
      `/team/${SCOPE}/resources/assets/item/${ASSET_ID}`,
    );
  });
});

describe('AssetNodeView — loadouts', () => {
  it('offers the loadouts from the detail row and writes the pick into node data', async () => {
    seedAndRender();
    const select = (await screen.findByTestId('asset-node-loadout')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3)); // None + 2
    fireEvent.change(select, { target: { value: LOADOUT_B } });
    expect(nodeData().loadout_id).toBe(LOADOUT_B);
  });

  it('prunes selections pinned to the loadout being left, keeps shared files', async () => {
    seedAndRender({
      ...NODE_DATA,
      loadout_id: LOADOUT_A,
      selected_file_ids: [SHEET_FILE, WORN_A_FILE],
    });
    const select = (await screen.findByTestId('asset-node-loadout')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    fireEvent.change(select, { target: { value: LOADOUT_B } });
    // WORN_A belongs to the outfit just left — shipping it into the next
    // generation would hand the model the wrong costume.
    expect(nodeData().selected_file_ids).toEqual([SHEET_FILE]);
  });

  it('shows no loadout picker for a non-character asset', async () => {
    fetchAssetDetail.mockResolvedValue({
      ...DETAIL,
      asset_type: 'prop',
      loadouts: [],
      files: [file({ slot: 'turnaround', resource_id: SHEET_FILE })],
    });
    seedAndRender({ ...NODE_DATA, asset_type: 'prop' });
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(screen.queryByTestId('asset-node-loadout')).toBeNull();
  });
});

describe('AssetNodeView — reference selection', () => {
  it('lists the primary slot first and marks it', async () => {
    seedAndRender();
    const list = await screen.findByTestId('asset-node-files');
    const boxes = list.querySelectorAll('input[type="checkbox"]');
    // Only the two loadout-free files show while no loadout is bound.
    expect(boxes.length).toBe(2);
    expect(boxes[0].getAttribute('data-testid')).toBe(`asset-node-file-${SHEET_FILE}`);
    expect(list.textContent).toContain('Primary');
  });

  it('a checkbox toggles selected_file_ids both ways', async () => {
    seedAndRender();
    const stills = await screen.findByTestId(`asset-node-file-${STILLS_FILE}`);
    fireEvent.click(stills);
    expect(nodeData().selected_file_ids).toEqual([SHEET_FILE, STILLS_FILE]);
    fireEvent.click(screen.getByTestId(`asset-node-file-${SHEET_FILE}`));
    expect(nodeData().selected_file_ids).toEqual([STILLS_FILE]);
  });

  it('withholds the checkboxes and the loadout picker in a read-only session', async () => {
    seedAndRender(NODE_DATA, { readOnly: true });
    const box = await screen.findByTestId(`asset-node-file-${SHEET_FILE}`);
    expect(box).toBeDisabled();
    expect(screen.getByTestId('asset-node-loadout')).toBeDisabled();
  });
});

describe('AssetNodeView — the asset is gone', () => {
  it('a 404 tombstones the node without unmounting it', async () => {
    fetchAssetDetail.mockRejectedValue(
      new GeneratedApiError(404, 'asset_not_found', 'gone'),
    );
    seedAndRender();
    await waitFor(() => expect(nodeData().removed).toBe(true));
    // The store now holds `removed`; re-render from it the way React Flow would.
    seedAndRender({ ...NODE_DATA, removed: true });
    expect(screen.getAllByTestId('asset-node-removed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Asset Removed').length).toBeGreaterThan(0);
    // The name snapshot survives so the user knows WHICH asset vanished.
    expect(screen.getAllByText('Cole Bannon').length).toBeGreaterThan(0);
  });

  it('renders the tombstone straight from node data, asking nothing', () => {
    seedAndRender({ ...NODE_DATA, removed: true });
    expect(screen.getByTestId('asset-node-removed')).toBeTruthy();
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });

  it('a network failure is NOT a removal', async () => {
    fetchAssetDetail.mockRejectedValue(
      new GeneratedApiError(NETWORK_STATUS, 'network', 'offline'),
    );
    seedAndRender();
    await waitFor(() => expect(screen.getByTestId('asset-node-load-error')).toBeTruthy());
    expect(nodeData().removed).toBeUndefined();
    expect(screen.queryByTestId('asset-node-removed')).toBeNull();
    // The snapshot is still on screen — the card did not go blank.
    expect(screen.getByText('Cole Bannon')).toBeTruthy();
  });

  it('a 403 is NOT a removal either', async () => {
    fetchAssetDetail.mockRejectedValue(
      new GeneratedApiError(403, 'not_a_member', 'forbidden'),
    );
    seedAndRender();
    await waitFor(() => expect(screen.getByTestId('asset-node-load-error')).toBeTruthy());
    expect(nodeData().removed).toBeUndefined();
  });
});

describe('orderedReferenceFiles', () => {
  const files = DETAIL.files;

  it('orders by the slot table, primary first, then sort_order', () => {
    const out = orderedReferenceFiles(files, 'character', LOADOUT_A);
    expect(out.map((f) => f.slot)).toEqual(['sheet', 'stills', 'worn']);
  });

  it('drops files pinned to another loadout, keeps loadout-free ones', () => {
    const out = orderedReferenceFiles(files, 'character', LOADOUT_A);
    expect(out.map((f) => f.resource_id)).not.toContain(WORN_B_FILE);
    expect(out.map((f) => f.resource_id)).toContain(WORN_A_FILE);
    expect(out.map((f) => f.resource_id)).toContain(SHEET_FILE);
  });

  it('with no loadout bound, every loadout-scoped file is out', () => {
    const out = orderedReferenceFiles(files, 'character', null);
    expect(out.map((f) => f.resource_id)).toEqual([SHEET_FILE, STILLS_FILE]);
  });

  it('a slot the type table does not name sorts last, not first', () => {
    const out = orderedReferenceFiles(
      [file({ resource_id: 'r9', slot: 'made_up' }), file({ resource_id: 'r1', slot: 'sheet' })],
      'character',
      null,
    );
    expect(out.map((f) => f.resource_id)).toEqual(['r1', 'r9']);
  });
});
