// features/canvas-core/smart/assetNode.test.ts
//
// The non-visual half of the asset smart node (P4 Task 4): the factory, the
// connect rules, the registry/width entries, and the one clipboard property
// the whole "copy node = copy reference" decision rests on.
//
// Ids are strings throughout because that is what `/api/v1/assets` puts on
// the wire for every BIGINT column — an `AssetRow` fixture with numeric ids
// would be testing a shape the backend never sends.

import { describe, expect, it } from 'vitest';

import { PRIMARY_SLOT } from '../../../components/assets/assetSlots';
import type { AssetFileRow, AssetRow } from '../../../services/assetsService';
import { cloneSubgraph } from '../store/clipboard';
import type { CanvasNode } from '../types';
import {
  _resetIdCounter,
  createAssetNode,
  primarySlotFileIds,
  type AssetNodeSeed,
} from './factories';
import { SMART_NODE_TYPES } from './nodes/registry';
import { canConnectSmart, SMART_NODE_DEFAULT_WIDTH } from './types';

const ASSET_ID = '727145299382534300';
const LOADOUT_A = '727145299382534401';
const LOADOUT_B = '727145299382534402';
const SHEET_FILE = '900000000000000001';
const SHEET_FILE_2 = '900000000000000005';
const STILLS_FILE = '900000000000000002';
const WORN_A = '900000000000000003';

const fixedSuffix = () => 'aaaa';

function fileRow(over: Partial<AssetFileRow> = {}): AssetFileRow {
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

function assetRow(over: Partial<AssetNodeSeed> = {}): AssetNodeSeed {
  const base: AssetRow = {
    id: ASSET_ID,
    scope_id: '727145299382534100',
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
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 0,
  };
  return { ...base, ...over };
}

describe('createAssetNode', () => {
  it('carries the wire ids and the display snapshot', () => {
    _resetIdCounter();
    const node = createAssetNode(assetRow(), {
      position: { x: 12, y: 34 },
      randomSuffix: fixedSuffix,
    });
    expect(node.type).toBe('asset');
    expect(node.id).toBe('asset-1-aaaa');
    expect(node.position).toEqual({ x: 12, y: 34 });
    expect(node.data.asset_id).toBe(ASSET_ID);
    expect(typeof node.data.asset_id).toBe('string');
    expect(node.data.loadout_id).toBeNull();
    expect(node.data.name).toBe('Cole Bannon');
    expect(node.data.asset_type).toBe('character');
    expect(node.data.cover_file_id).toBe(SHEET_FILE);
    expect(node.data.readiness_state).toBe('ready');
  });

  it('does NOT seed `removed` — absent means "not known to be gone"', () => {
    const node = createAssetNode(assetRow(), { randomSuffix: fixedSuffix });
    expect('removed' in node.data).toBe(false);
  });

  it('seeds selected_file_ids from the primary slot, in sort_order', () => {
    const node = createAssetNode(
      assetRow({
        files: [
          fileRow({ resource_id: SHEET_FILE_2, sort_order: 1 }),
          fileRow({ resource_id: SHEET_FILE, sort_order: 0 }),
          fileRow({ resource_id: STILLS_FILE, slot: 'stills' }),
        ],
      }),
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.selected_file_ids).toEqual([SHEET_FILE, SHEET_FILE_2]);
  });

  it('binds a loadout and keeps its files plus the shared ones', () => {
    const node = createAssetNode(
      assetRow({
        files: [
          fileRow({ resource_id: SHEET_FILE }),
          fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_A, sort_order: 1 }),
          fileRow({ resource_id: 'x', loadout_id: LOADOUT_B, sort_order: 2 }),
        ],
      }),
      { loadoutId: LOADOUT_A, randomSuffix: fixedSuffix },
    );
    expect(node.data.loadout_id).toBe(LOADOUT_A);
    expect(node.data.selected_file_ids).toEqual([SHEET_FILE, WORN_A]);
  });

  it('a summary row with no `files` still produces a valid node', () => {
    const node = createAssetNode(assetRow(), { randomSuffix: fixedSuffix });
    expect(node.data.selected_file_ids).toEqual([]);
  });

  it('a prompt asset seeds nothing — it has no primary FILE slot', () => {
    expect(PRIMARY_SLOT.prompt).toBeNull();
    const node = createAssetNode(
      assetRow({
        asset_type: 'prompt',
        files: [fileRow({ slot: 'examples', resource_id: 'e1' })],
      }),
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.selected_file_ids).toEqual([]);
  });

  it('a draft asset is snapshotted as draft, not defaulted to ready', () => {
    const node = createAssetNode(
      assetRow({ readiness: { state: 'draft', missing: ['sheet'] } }),
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.readiness_state).toBe('draft');
  });
});

describe('primarySlotFileIds', () => {
  it('is empty for every type whose primary slot is null', () => {
    for (const [type, slot] of Object.entries(PRIMARY_SLOT)) {
      if (slot !== null) continue;
      expect(
        primarySlotFileIds(
          assetRow({
            asset_type: type as AssetRow['asset_type'],
            files: [fileRow({ slot: 'anything' })],
          }),
          null,
        ),
      ).toEqual([]);
    }
  });

  it('excludes a file pinned to a different loadout', () => {
    const files = [fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_B })];
    expect(primarySlotFileIds(assetRow({ files }), LOADOUT_A)).toEqual([]);
    expect(primarySlotFileIds(assetRow({ files }), LOADOUT_B)).toEqual([WORN_A]);
  });

  it('with no loadout bound, loadout-scoped files are left out', () => {
    const files = [
      fileRow({ resource_id: SHEET_FILE }),
      fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_A }),
    ];
    // `loadoutId === null` keeps everything the card could legally use; a
    // loadout-scoped primary file only belongs once that outfit is bound.
    expect(primarySlotFileIds(assetRow({ files }), null)).toEqual([
      SHEET_FILE,
      WORN_A,
    ]);
  });
});

describe('canConnectSmart — asset rules', () => {
  it.each(['prompt', 'shot', 'llm'])('asset → %s is allowed', (target) => {
    expect(canConnectSmart('asset', target)).toBe(true);
  });

  it.each(['output', 'loop', 'group', 'media', 'timeline', 'character', 'asset'])(
    'asset → %s is refused',
    (target) => {
      expect(canConnectSmart('asset', target)).toBe(false);
    },
  );

  it.each([
    'shot',
    'media',
    'prompt',
    'output',
    'loop',
    'timeline',
    'group',
    'llm',
    'character',
    'location',
    'prop',
    'asset',
  ])('%s → asset is refused', (source) => {
    expect(canConnectSmart(source, 'asset')).toBe(false);
  });

  it('leaves the pre-existing rules alone', () => {
    // A regression fence: the asset arm is stated BEFORE the `→ shot`
    // blanket refusal, and getting that ordering wrong is exactly what would
    // open `media → shot` or close `shot → prompt`.
    expect(canConnectSmart('shot', 'prompt')).toBe(true);
    expect(canConnectSmart('shot', 'loop')).toBe(true);
    expect(canConnectSmart('media', 'prompt')).toBe(true);
    expect(canConnectSmart('media', 'shot')).toBe(false);
    expect(canConnectSmart('prompt', 'output')).toBe(true);
    expect(canConnectSmart('output', 'group')).toBe(true);
    expect(canConnectSmart('output', 'media')).toBe(false);
    expect(canConnectSmart('character', 'prompt')).toBe(true);
    expect(canConnectSmart('character', 'loop')).toBe(false);
    expect(canConnectSmart('llm', 'llm')).toBe(true);
    expect(canConnectSmart('loop', 'output')).toBe(false);
  });
});

describe('registry + width table', () => {
  it('the asset type has a renderer', () => {
    expect(SMART_NODE_TYPES.asset).toBeTypeOf('function');
  });

  it('every registered type has a default width', () => {
    for (const type of Object.keys(SMART_NODE_TYPES)) {
      expect(
        SMART_NODE_DEFAULT_WIDTH[type as keyof typeof SMART_NODE_DEFAULT_WIDTH],
      ).toBeGreaterThan(0);
    }
  });
});

describe('clipboard — copying a node copies the REFERENCE', () => {
  const assetNode = (id: string): CanvasNode =>
    ({
      id,
      type: 'asset',
      position: { x: 0, y: 0 },
      data: {
        asset_id: ASSET_ID,
        loadout_id: LOADOUT_A,
        selected_file_ids: [SHEET_FILE, WORN_A],
        name: 'Cole Bannon',
        asset_type: 'character',
        cover_file_id: SHEET_FILE,
        readiness_state: 'ready',
      },
    }) as CanvasNode;

  it('preserves asset_id / loadout_id / selected_file_ids verbatim', () => {
    const { nodes } = cloneSubgraph([assetNode('a1')], [], new Set(['a1']), 40);
    const data = (nodes[0] as Record<string, any>).data;
    expect((nodes[0] as Record<string, unknown>).id).not.toBe('a1');
    expect(data.asset_id).toBe(ASSET_ID);
    expect(data.loadout_id).toBe(LOADOUT_A);
    expect(data.selected_file_ids).toEqual([SHEET_FILE, WORN_A]);
  });

  it('does not treat the asset fields as node-id tags to remap or strip', () => {
    // `remapSmartTags` re-points (or DELETES) every data field it recognises
    // as a node-id reference. Adding asset_id to that set would silently
    // unbind the copy — the card would render as an empty asset node and the
    // backend's `canvas_asset_refs` mirror would lose the row on the next
    // save. The clone here brings no referents along, so anything that IS in
    // that set gets deleted; these keys surviving is the proof they are not.
    const { nodes } = cloneSubgraph([assetNode('a1')], [], new Set(), 0);
    const data = (nodes[0] as Record<string, any>).data;
    expect(Object.keys(data).sort()).toEqual(
      [
        'asset_id',
        'asset_type',
        'cover_file_id',
        'loadout_id',
        'name',
        'readiness_state',
        'selected_file_ids',
      ].sort(),
    );
  });

  it('the clone does not alias the original arrays', () => {
    const source = assetNode('a1');
    const { nodes } = cloneSubgraph([source], [], new Set(), 0);
    const cloned = (nodes[0] as Record<string, any>).data;
    cloned.selected_file_ids.push('999');
    expect((source as Record<string, any>).data.selected_file_ids).toEqual([
      SHEET_FILE,
      WORN_A,
    ]);
  });
});
