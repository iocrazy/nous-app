// features/canvas-core/library/referenceCut.test.ts
//
// "Which of this asset's files does the provider actually get?", asked before
// the asset is on the board. What is worth pinning is the ORDER (delivery, not
// declaration — otherwise the table dims a file that WAS sent) and the three
// readings of the ceiling: a number, UNKNOWN, and a real zero.

import { describe, expect, it } from 'vitest';

import { referenceCut } from './referenceCut';

/** `asset_files` row — the real wire shape, every id a string. */
const file = (rid: string, slot: string, sort: number) => ({
  asset_id: '727145299382534300',
  resource_id: rid,
  slot,
  loadout_id: null,
  sort_order: sort,
  note: null,
  attached_by: null,
  attached_at: '2026-09-01T00:00:00Z',
});

// `sheet` is a character's PRIMARY slot and `worn` / `stills` are two more it
// really declares (`components/assets/assetSlots.ts`). A made-up slot name
// would sort into the unnamed tail and pin nothing about delivery order.
//
// Listed in DECLARATION order (`SLOTS.character` puts `worn` LAST), which is
// NOT the order they are delivered in. That gap is the whole point: an
// implementation that just walks the input array — or sorts by the type's
// declared slots — comes out in a different order than the provider receives,
// and the ordering case below is what catches it. A fixture already in
// delivery order would let that mutation pass.
const FOUR = [
  file('600000000000000001', 'sheet', 0),
  file('600000000000000002', 'sheet', 1),
  file('600000000000000004', 'stills', 0),
  file('600000000000000003', 'worn', 0),
];

describe('referenceCut', () => {
  it('spec §4 item 7 — max_refs 3 over 4 files gives 3 Sends and 1 Cut', () => {
    const rows = referenceCut(FOUR, 'character', null, 3);
    expect(rows.map((r) => r.sends)).toEqual([true, true, true, false]);
  });

  it('an unknown ceiling sends everything — null means UNKNOWN, never zero', () => {
    expect(referenceCut(FOUR, 'character', null, null).every((r) => r.sends)).toBe(true);
  });

  it('a ceiling of 0 cuts everything, and says so rather than showing an empty table', () => {
    const rows = referenceCut(FOUR, 'character', null, 0);
    expect(rows).toHaveLength(4);
    expect(rows.every((r) => r.sends)).toBe(false);
  });

  it('rows are in DELIVERY order, so the cut is always the tail', () => {
    // `worn` is declared LAST for a character but delivered SECOND — drawing
    // declaration order would dim a file that IS sent.
    expect(referenceCut(FOUR, 'character', null, 4).map((r) => r.slot)).toEqual([
      'sheet',
      'sheet',
      'worn',
      'stills',
    ]);
  });

  it('carries the resource id, which is the vocabulary the bundle answers in', () => {
    // ...003 is `worn` and ...004 is `stills`: third and fourth on the wire,
    // fourth and third in the fixture.
    expect(referenceCut(FOUR, 'character', null, 4).map((r) => r.resourceId)).toEqual([
      '600000000000000001',
      '600000000000000002',
      '600000000000000003',
      '600000000000000004',
    ]);
  });

  it('a loadout-pinned file is absent when no loadout is bound', () => {
    const withOutfit = [...FOUR, { ...file('600000000000000005', 'worn', 1), loadout_id: 'lo1' }];
    expect(referenceCut(withOutfit, 'character', null, 10)).toHaveLength(4);
  });
});
