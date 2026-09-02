// features/canvas-core/smart/assetFiles.test.ts
//
// The shared answer to "which files does an asset card reference" (P4 Task 4,
// fix round 1).
//
// The case that matters most is the CONSISTENCY one at the bottom. Seeding
// and rendering used to answer this question in two places with two
// predicates, and they disagreed on exactly one input: a loadout-pinned file
// in the primary slot with no loadout bound — which is what both entry points
// produce, since neither passes a `loadoutId`. The card selected a reference
// it then refused to draw a row for, so the user could not uncheck it and
// Task 5 would have sent it to the model anyway.
//
// Both halves were pinned at the time, which is why it shipped: the old
// assertion was titled "with no loadout bound, loadout-scoped files are left
// out" while its body asserted they were kept. A title that contradicts its
// own assertion is worse than no test — it tells the next reader the opposite
// of what is being checked. Both directions are stated explicitly below.
//
// Ids are strings: that is what `/api/v1/assets` puts on the wire for every
// BIGINT column.

import { describe, expect, it } from 'vitest';

import { ASSET_TYPES, PRIMARY_SLOT } from '../../../components/assets/assetSlots';
import type { AssetFileRow, AssetRow } from '../../../services/assetsService';
import {
  fileVisibleUnderLoadout,
  orderedReferenceFiles,
  primarySlotFileIds,
  type AssetNodeSeed,
} from './assetFiles';

const ASSET_ID = '727145299382534300';
const LOADOUT_A = '727145299382534401';
const LOADOUT_B = '727145299382534402';
const SHEET_FILE = '900000000000000001';
const SHEET_FILE_2 = '900000000000000005';
const STILLS_FILE = '900000000000000002';
const WORN_A = '900000000000000003';
const WORN_B = '900000000000000004';

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

describe('fileVisibleUnderLoadout', () => {
  it('a file with no loadout is usable under every one, and under none', () => {
    const shared = fileRow({ loadout_id: null });
    expect(fileVisibleUnderLoadout(shared, null)).toBe(true);
    expect(fileVisibleUnderLoadout(shared, LOADOUT_A)).toBe(true);
    expect(fileVisibleUnderLoadout(shared, LOADOUT_B)).toBe(true);
  });

  it('a pinned file is usable under ITS loadout only', () => {
    const pinned = fileRow({ loadout_id: LOADOUT_A });
    expect(fileVisibleUnderLoadout(pinned, LOADOUT_A)).toBe(true);
    expect(fileVisibleUnderLoadout(pinned, LOADOUT_B)).toBe(false);
  });

  it('with NO loadout bound, a pinned file is not usable', () => {
    // The half the factory used to get wrong. A card with no outfit chosen
    // means "this character, generically" — an outfit-specific still is not
    // an answer to that, and handing it downstream is the wrong-costume bug.
    expect(fileVisibleUnderLoadout(fileRow({ loadout_id: LOADOUT_A }), null)).toBe(
      false,
    );
  });
});

describe('primarySlotFileIds', () => {
  it('takes the primary slot only, in sort_order', () => {
    expect(
      primarySlotFileIds(
        assetRow({
          files: [
            fileRow({ resource_id: SHEET_FILE_2, sort_order: 1 }),
            fileRow({ resource_id: SHEET_FILE, sort_order: 0 }),
            fileRow({ resource_id: STILLS_FILE, slot: 'stills' }),
          ],
        }),
        null,
      ),
    ).toEqual([SHEET_FILE, SHEET_FILE_2]);
  });

  it('is empty for every type whose primary slot is null', () => {
    for (const type of ASSET_TYPES) {
      if (PRIMARY_SLOT[type] !== null) continue;
      expect(
        primarySlotFileIds(
          assetRow({ asset_type: type, files: [fileRow({ slot: 'anything' })] }),
          null,
        ),
      ).toEqual([]);
    }
  });

  it('a summary row with no `files` seeds nothing rather than throwing', () => {
    expect(primarySlotFileIds(assetRow(), null)).toEqual([]);
  });

  it('with a loadout bound, another outfit\'s primary file is left out', () => {
    const files = [
      fileRow({ resource_id: SHEET_FILE }),
      fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_A, sort_order: 1 }),
      fileRow({ resource_id: WORN_B, loadout_id: LOADOUT_B, sort_order: 2 }),
    ];
    expect(primarySlotFileIds(assetRow({ files }), LOADOUT_A)).toEqual([
      SHEET_FILE,
      WORN_A,
    ]);
  });

  it('with NO loadout bound, loadout-scoped files are left out', () => {
    // The assertion this test's NAME always claimed. It used to assert the
    // opposite — see this file's header.
    const files = [
      fileRow({ resource_id: SHEET_FILE }),
      fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_A, sort_order: 1 }),
    ];
    expect(primarySlotFileIds(assetRow({ files }), null)).toEqual([SHEET_FILE]);
  });

  it('and the inverse: binding that loadout brings the pinned file back', () => {
    const files = [
      fileRow({ resource_id: SHEET_FILE }),
      fileRow({ resource_id: WORN_A, loadout_id: LOADOUT_A, sort_order: 1 }),
    ];
    expect(primarySlotFileIds(assetRow({ files }), LOADOUT_A)).toEqual([
      SHEET_FILE,
      WORN_A,
    ]);
  });
});

describe('orderedReferenceFiles', () => {
  const FILES = [
    fileRow({ resource_id: STILLS_FILE, slot: 'stills', sort_order: 1 }),
    fileRow({ resource_id: SHEET_FILE, slot: 'sheet', sort_order: 0 }),
    fileRow({ resource_id: WORN_A, slot: 'worn', loadout_id: LOADOUT_A }),
    fileRow({ resource_id: WORN_B, slot: 'worn', loadout_id: LOADOUT_B }),
  ];

  it('orders by the slot table, primary first, then sort_order', () => {
    expect(
      orderedReferenceFiles(FILES, 'character', LOADOUT_A).map((f) => f.slot),
    ).toEqual(['sheet', 'stills', 'worn']);
  });

  it('drops files pinned to another loadout, keeps the shared ones', () => {
    const ids = orderedReferenceFiles(FILES, 'character', LOADOUT_A).map(
      (f) => f.resource_id,
    );
    expect(ids).toContain(SHEET_FILE);
    expect(ids).toContain(WORN_A);
    expect(ids).not.toContain(WORN_B);
  });

  it('with no loadout bound, every loadout-scoped file is out', () => {
    expect(
      orderedReferenceFiles(FILES, 'character', null).map((f) => f.resource_id),
    ).toEqual([SHEET_FILE, STILLS_FILE]);
  });

  it('a slot the type table does not name sorts last, not first', () => {
    expect(
      orderedReferenceFiles(
        [
          fileRow({ resource_id: 'r9', slot: 'made_up' }),
          fileRow({ resource_id: 'r1', slot: 'sheet' }),
        ],
        'character',
        null,
      ).map((f) => f.resource_id),
    ).toEqual(['r1', 'r9']);
  });

  it('one resource attached to two slots draws ONE row, in the better slot', () => {
    // `asset_files` is keyed (asset_id, resource_id, slot), so this is a legal
    // pair of rows. The selection vocabulary is RESOURCES, so two checkboxes
    // would tick and untick together — and share a data-testid.
    const out = orderedReferenceFiles(
      [
        fileRow({ resource_id: SHEET_FILE, slot: 'stills', sort_order: 3 }),
        fileRow({ resource_id: SHEET_FILE, slot: 'sheet', sort_order: 0 }),
      ],
      'character',
      null,
    );
    expect(out).toHaveLength(1);
    expect(out[0].slot).toBe('sheet');
  });
});

describe('seeding and rendering cannot disagree', () => {
  // The anti-regression fence for the divergence this module exists to close:
  // whatever the factory pre-selects, the checklist must offer a row for. If
  // it does not, the user is holding a reference they cannot see or remove.
  const FILES = [
    fileRow({ resource_id: SHEET_FILE, slot: 'sheet' }),
    fileRow({ resource_id: SHEET_FILE_2, slot: 'sheet', loadout_id: LOADOUT_A }),
    fileRow({ resource_id: WORN_A, slot: 'worn', loadout_id: LOADOUT_A }),
    fileRow({ resource_id: WORN_B, slot: 'worn', loadout_id: LOADOUT_B }),
    fileRow({ resource_id: STILLS_FILE, slot: 'stills' }),
  ];

  it.each([
    ['no loadout', null],
    ['loadout A', LOADOUT_A],
    ['loadout B', LOADOUT_B],
  ])('every seeded id has a checkbox — %s', (_label, loadoutId) => {
    const asset = assetRow({ files: FILES });
    const seeded = primarySlotFileIds(asset, loadoutId as string | null);
    const rendered = new Set(
      orderedReferenceFiles(FILES, 'character', loadoutId as string | null).map(
        (f) => f.resource_id,
      ),
    );
    expect(seeded.filter((id) => !rendered.has(id))).toEqual([]);
  });

  it('the no-loadout case actually exercises a pinned primary file', () => {
    // Without this the case above could pass on a fixture that has no pinned
    // primary file at all — the exact input the bug needed.
    expect(
      FILES.some(
        (f) => f.slot === PRIMARY_SLOT.character && f.loadout_id !== null,
      ),
    ).toBe(true);
  });
});
