// features/canvas-core/smart/assetPlacement.test.ts
//
// Where asset cards land when nothing points at a spot (P4 Task 6).
//
// Ids are strings throughout — that is what `/api/v1/assets` puts on the wire
// for every BIGINT column, and a fixture with numeric ids would be pinning a
// shape the backend never sends.

import { describe, expect, it } from 'vitest';

import type { AssetRow, AssetType } from '../../../services/assetsService';
import type { CanvasNode } from '../types';
import {
  assetIdsOnCanvas,
  buildProjectAssetNodes,
  INSERT_GAP_X,
  LANE_STEP_X,
  LANE_STEP_Y,
  layoutAssetLanes,
  nextFreePosition,
} from './assetPlacement';
import { _resetIdCounter } from './factories';
import { SMART_NODE_DEFAULT_WIDTH } from './types';

const SCOPE = '727145299382534100';

function assetRow(id: string, type: AssetType, name = 'Row'): AssetRow {
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
    in_library: true,
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
}

const node = (over: Record<string, unknown>): CanvasNode => ({
  id: 'n1',
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: {},
  ...over,
});

describe('nextFreePosition', () => {
  it('answers the origin for an empty canvas', () => {
    expect(nextFreePosition([])).toEqual({ x: 0, y: 0 });
  });

  it('clears the widest node, using the type default when unmeasured', () => {
    const nodes = [
      node({ id: 'a', type: 'prompt', position: { x: 100, y: 40 } }),
      node({ id: 'b', type: 'output', position: { x: 400, y: 900 } }),
    ];
    // prompt right edge 100+320=420; output right edge 400+260=660.
    expect(nextFreePosition(nodes)).toEqual({
      x: 660 + INSERT_GAP_X,
      y: 40,
    });
  });

  it('prefers a measured width over the type default', () => {
    const wide = node({ id: 'a', type: 'prompt', position: { x: 0, y: 0 }, width: 900 });
    expect(nextFreePosition([wide]).x).toBe(900 + INSERT_GAP_X);
    expect(SMART_NODE_DEFAULT_WIDTH.prompt).toBeLessThan(900);
  });

  it('treats an unreadable document as empty rather than throwing', () => {
    const broken = [{ id: 'x' }, { id: 'y', position: 'nope' }] as unknown as CanvasNode[];
    expect(nextFreePosition(broken)).toEqual({ x: 0, y: 0 });
  });
});

describe('assetIdsOnCanvas', () => {
  it('collects asset ids and ignores every other node type', () => {
    const nodes = [
      node({ id: 'a', type: 'asset', data: { asset_id: '11' } }),
      node({ id: 'b', type: 'asset', data: { asset_id: '22' } }),
      node({ id: 'c', type: 'prompt', data: { asset_id: '33' } }),
      node({ id: 'd', type: 'asset', data: {} }),
    ];
    expect([...assetIdsOnCanvas(nodes)].sort()).toEqual(['11', '22']);
  });
});

describe('layoutAssetLanes', () => {
  it('puts the four asset kinds in adjacent lanes, in ASSET_TYPES order', () => {
    const slots = layoutAssetLanes(
      [
        assetRow('4', 'costume'),
        assetRow('1', 'character'),
        assetRow('3', 'prop'),
        assetRow('2', 'location'),
      ],
      { x: 1000, y: 20 },
    );
    const laneOf = (id: string) => slots.find((s) => s.item.id === id)!;
    expect(laneOf('1').lane).toBe(0);
    expect(laneOf('2').lane).toBe(1);
    expect(laneOf('3').lane).toBe(2);
    expect(laneOf('4').lane).toBe(3);
    expect(laneOf('3').position).toEqual({ x: 1000 + 2 * LANE_STEP_X, y: 20 });
  });

  it('stacks a lane downwards, in the order the rows arrived', () => {
    const slots = layoutAssetLanes(
      [assetRow('1', 'character', 'A'), assetRow('2', 'character', 'B')],
      { x: 0, y: 0 },
    );
    expect(slots.map((s) => s.position)).toEqual([
      { x: 0, y: 0 },
      { x: 0, y: LANE_STEP_Y },
    ]);
  });

  it('gives an absent type NO lane, so four kinds really are four columns', () => {
    // A project with characters and props but no locations must not leave an
    // empty column where locations would have been.
    const slots = layoutAssetLanes(
      [assetRow('1', 'character'), assetRow('3', 'prop')],
      { x: 0, y: 0 },
    );
    expect(slots.map((s) => s.lane)).toEqual([0, 1]);
  });

  it('places prompt and audio rows too, rather than dropping them', () => {
    // The plan names four lanes; a project can still link a prompt or an
    // audio asset, and placing four of six types would be an insert that
    // silently did less than it said.
    const slots = layoutAssetLanes(
      [assetRow('1', 'character'), assetRow('5', 'prompt'), assetRow('6', 'audio')],
      { x: 0, y: 0 },
    );
    expect(slots.map((s) => s.assetType)).toEqual(['character', 'prompt', 'audio']);
    expect(slots).toHaveLength(3);
  });
});

describe('buildProjectAssetNodes', () => {
  it('places one asset card per row, to the right of what is there', () => {
    _resetIdCounter();
    const existing = [node({ id: 'a', type: 'prompt', position: { x: 0, y: 0 } })];
    const plan = buildProjectAssetNodes(
      [assetRow('11', 'character', 'Cole'), assetRow('22', 'location', 'Dock')],
      existing,
    );
    expect(plan.inserted).toBe(2);
    expect(plan.skipped).toBe(0);
    expect(plan.nodes).toHaveLength(2);
    expect(plan.nodes.every((n) => (n as { type: string }).type === 'asset')).toBe(true);
    const first = plan.nodes[0] as { position: { x: number }; data: { asset_id: string } };
    expect(first.data.asset_id).toBe('11');
    expect(typeof first.data.asset_id).toBe('string');
    expect(first.position.x).toBe(SMART_NODE_DEFAULT_WIDTH.prompt + INSERT_GAP_X);
  });

  it('skips an asset a card on this canvas already points at', () => {
    _resetIdCounter();
    const existing = [
      node({ id: 'a', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: '11' } }),
    ];
    const plan = buildProjectAssetNodes(
      [assetRow('11', 'character'), assetRow('22', 'location')],
      existing,
    );
    expect(plan.inserted).toBe(1);
    expect(plan.skipped).toBe(1);
    expect((plan.nodes[0] as { data: { asset_id: string } }).data.asset_id).toBe('22');
  });

  it('creates nothing at all when every asset is already present', () => {
    const existing = [
      node({ id: 'a', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: '11' } }),
    ];
    const plan = buildProjectAssetNodes([assetRow('11', 'character')], existing);
    expect(plan.nodes).toEqual([]);
    expect(plan.inserted).toBe(0);
    expect(plan.skipped).toBe(1);
  });

  it('seeds an EMPTY selection from a summary row, and says so by shape', () => {
    // `GET /projects/{id}/assets` answers `file_counts_by_slot`, not file rows,
    // so a bulk-inserted card starts referencing nothing and the user ticks
    // what they want on the card itself. Pinned so a later change that starts
    // pre-selecting has to be deliberate.
    _resetIdCounter();
    const plan = buildProjectAssetNodes([assetRow('11', 'character')], []);
    expect((plan.nodes[0] as { data: { selected_file_ids: string[] } }).data
      .selected_file_ids).toEqual([]);
  });
});
