// features/canvas-core/library/referenceCut.test.ts
//
// "Which of this asset's files does the provider actually get?", asked before
// the asset is on the board. Three things are worth pinning: the POPULATION
// (the primary slot — what both `addReferences` and `assetNodeData` really
// send, not every attached file), the RANK the ceiling trims from, and the
// three readings of that ceiling — a number, UNKNOWN, and a real zero.

import { describe, expect, it } from 'vitest';

import type { AssetNodeSeed } from '../smart/assetFiles';
import { otherSlotCount, referenceCut } from './referenceCut';

const ASSET_ID = '727145299382534300';

/** One `asset_files` row — the real wire shape, every id a string. */
const file = (rid: string, slot: string, sort: number, loadoutId: string | null = null) => ({
  asset_id: ASSET_ID,
  resource_id: rid,
  slot,
  loadout_id: loadoutId,
  sort_order: sort,
  note: null,
  attached_by: null,
  attached_at: '2026-09-01T00:00:00Z',
});

/**
 * `GET /assets/{id}` in full, so this asks `primarySlotFileIds` with exactly
 * the argument `addReferences.ts` and `assetNodeData` ask it with. A
 * hand-narrowed `{ asset_type, files }` would let the two drift.
 */
const asset = (files: ReturnType<typeof file>[]): AssetNodeSeed =>
  ({
    id: ASSET_ID,
    scope_id: '727145299382534100',
    asset_type: 'character' as const,
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
    cover_file_id: files[0]?.resource_id ?? null,
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    in_library: true,
    tags: {},
    sort_order: 0,
    created_by: null,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    readiness: { state: 'ready' as const, missing: [] },
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 0,
    files,
  }) as AssetNodeSeed;

// `sheet` IS a character's primary slot, on both sides (`PRIMARY_SLOT` here and
// `slots.py` there), so these four are exactly what a pick delivers.
const FOUR_PRIMARY = asset([
  file('600000000000000001', 'sheet', 0),
  file('600000000000000002', 'sheet', 1),
  file('600000000000000003', 'sheet', 2),
  file('600000000000000004', 'sheet', 3),
]);

// The mixed shape: two primary files and two that a pick leaves behind.
const MIXED = asset([
  file('600000000000000001', 'sheet', 0),
  file('600000000000000002', 'sheet', 1),
  file('600000000000000004', 'stills', 0),
  file('600000000000000003', 'worn', 0),
]);

describe('referenceCut', () => {
  it('spec §4 item 7 — max_refs 3 over 4 files gives 3 Sends and 1 Cut', () => {
    const rows = referenceCut(FOUR_PRIMARY, null, 3);
    expect(rows.map((r) => r.sends)).toEqual([true, true, true, false]);
  });

  it('ranks only what a pick SENDS — the other slots are not in the table', () => {
    // `addReferences` mints one ref per `primarySlotFileIds`, so a table over
    // every attached file would say `worn` Sends and `stills` is Cut about an
    // asset whose pick delivers neither.
    const rows = referenceCut(MIXED, null, 3);
    expect(rows.map((r) => r.slot)).toEqual(['sheet', 'sheet']);
    expect(rows.map((r) => r.sends)).toEqual([true, true]);
  });

  it('an unknown ceiling sends everything — null means UNKNOWN, never zero', () => {
    expect(referenceCut(FOUR_PRIMARY, null, null).every((r) => r.sends)).toBe(true);
  });

  it('a ceiling of 0 cuts everything, and says so rather than showing an empty table', () => {
    const rows = referenceCut(FOUR_PRIMARY, null, 0);
    expect(rows).toHaveLength(4);
    expect(rows.every((r) => r.sends)).toBe(false);
  });

  it('rows are in DELIVERY rank, so the cut is always the tail', () => {
    // Handed to it OUT of `sort_order`. Rendering the array as it arrived would
    // dim ...002, which is third on the wire and therefore sent at max 3.
    const shuffled = asset([
      file('600000000000000004', 'sheet', 3),
      file('600000000000000001', 'sheet', 0),
      file('600000000000000003', 'sheet', 2),
      file('600000000000000002', 'sheet', 1),
    ]);
    const rows = referenceCut(shuffled, null, 3);
    expect(rows.map((r) => r.resourceId)).toEqual([
      '600000000000000001',
      '600000000000000002',
      '600000000000000003',
      '600000000000000004',
    ]);
    expect(rows.map((r) => r.sends)).toEqual([true, true, true, false]);
  });

  it('a loadout-pinned file joins the table only while that loadout is bound', () => {
    const withOutfit = asset([
      file('600000000000000001', 'sheet', 0),
      file('600000000000000005', 'sheet', 1, 'lo1'),
    ]);
    expect(referenceCut(withOutfit, null, 10)).toHaveLength(1);
    expect(referenceCut(withOutfit, 'lo1', 10).map((r) => r.resourceId)).toEqual([
      '600000000000000001',
      '600000000000000005',
    ]);
  });
});

describe('otherSlotCount', () => {
  it('counts what the pick leaves behind, so a short table is not a silent one', () => {
    expect(otherSlotCount(MIXED, null)).toBe(2);
  });

  it('is zero when every file is delivered', () => {
    expect(otherSlotCount(FOUR_PRIMARY, null)).toBe(0);
  });

  it('counts by resource, not by attachment row', () => {
    // The same resource in two slots is ONE choice — `asset_files` is keyed
    // (asset_id, resource_id, slot), and the selection vocabulary is resources.
    const twice = asset([
      file('600000000000000001', 'sheet', 0),
      file('600000000000000004', 'stills', 0),
      file('600000000000000004', 'extras', 0),
    ]);
    expect(otherSlotCount(twice, null)).toBe(1);
  });

  it('a loadout-pinned file nobody bound is left behind too', () => {
    const withOutfit = asset([
      file('600000000000000001', 'sheet', 0),
      file('600000000000000005', 'sheet', 1, 'lo1'),
    ]);
    expect(otherSlotCount(withOutfit, null)).toBe(1);
    expect(otherSlotCount(withOutfit, 'lo1')).toBe(0);
  });
});
