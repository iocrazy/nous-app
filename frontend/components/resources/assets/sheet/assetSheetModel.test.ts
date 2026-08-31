/**
 * The entity sheet's arithmetic, tested without rendering anything.
 *
 * Two of these carry more weight than the rest:
 *
 *  * `moveSlot` IS the drag-and-drop behaviour. JSDOM cannot produce a real
 *    pointer stream, so a synthetic-event test would be pinning the harness
 *    rather than the reorder; the exported pure function is what the Board
 *    calls and what persistence writes.
 *  * The `worn` filter is the loadout switch. A file with no `loadout_id`
 *    belongs to no outfit and must survive every switch — the alternative
 *    (hiding it) makes a file the user attached disappear with no way to see
 *    that it is still there.
 */

import { describe, expect, it } from 'vitest';

import {
  audioDurationSec,
  audioLoopable,
  audioRelationFor,
  boardPins,
  boardSlotOrder,
  canvasKindFor,
  composeLoadoutNegative,
  composeLoadoutPrompt,
  defaultLoadoutId,
  filesForSlot,
  formatDuration,
  hasLoadouts,
  linkRowsFor,
  moveSlot,
  deepEqual,
  platformParamRows,
  platformParamsFromRows,
  primaryFile,
  promptPlaceholders,
  relatedAssetIds,
  relationSectionsFor,
  savedSlotOrder,
} from './assetSheetModel';
import {
  AUDIO_DETAIL,
  CHARACTER_DETAIL,
  COSTUME_DETAIL,
  PRESET_PROMPT_DETAIL,
  makeDetail,
  makeFile,
  makeLoadout,
  makeRow,
} from './assetSheetFixtures';

describe('savedSlotOrder', () => {
  it('reads attrs.board_layout.slot_order', () => {
    expect(savedSlotOrder({ board_layout: { slot_order: ['worn', 'stills'] } })).toEqual([
      'worn',
      'stills',
    ]);
  });

  it('is empty for every malformed shape rather than throwing', () => {
    // `attrs` is free jsonb: an older build, a hand edit or another client can
    // put anything there, and a board that throws makes the asset unopenable.
    expect(savedSlotOrder(undefined)).toEqual([]);
    expect(savedSlotOrder({})).toEqual([]);
    expect(savedSlotOrder({ board_layout: 'nope' })).toEqual([]);
    expect(savedSlotOrder({ board_layout: { slot_order: 'stills' } })).toEqual([]);
    expect(savedSlotOrder({ board_layout: { slot_order: ['stills', 7] } })).toEqual(['stills']);
  });
});

describe('boardSlotOrder', () => {
  it('excludes the primary slot and includes unsorted', () => {
    const order = boardSlotOrder('character', {});
    expect(order).toEqual(['stills', 'expressions', 'extras', 'worn', 'unsorted']);
    expect(order).not.toContain('sheet');
  });

  it('honours a saved order', () => {
    expect(boardSlotOrder('character', { board_layout: { slot_order: ['worn', 'stills'] } })).toEqual(
      ['worn', 'stills', 'expressions', 'extras', 'unsorted'],
    );
  });

  it('appends a slot the saved layout predates instead of hiding it', () => {
    // The load-bearing case: a layout written before a slot existed must not
    // make the new slot permanently invisible.
    const order = boardSlotOrder('character', {
      board_layout: { slot_order: ['stills', 'expressions', 'extras', 'worn', 'unsorted'] },
    });
    expect(order).toContain('worn');
    const partial = boardSlotOrder('character', { board_layout: { slot_order: ['worn'] } });
    expect(partial).toEqual(['worn', 'stills', 'expressions', 'extras', 'unsorted']);
  });

  it('drops a saved slot the type no longer has', () => {
    // 'ghost' is not a prop slot; 'turnaround' is the primary and lives in
    // the big frame, so neither belongs in the grid.
    expect(boardSlotOrder('prop', { board_layout: { slot_order: ['ghost', 'details'] } })).toEqual([
      'details',
      'in_scene',
      'unsorted',
    ]);
  });

  it('prompt has only examples plus unsorted (its primary is the body)', () => {
    expect(boardSlotOrder('prompt', {})).toEqual(['examples', 'unsorted']);
  });
});

describe('moveSlot', () => {
  const order = ['a', 'b', 'c', 'd'];

  it('moves forward and backward', () => {
    expect(moveSlot(order, 0, 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(moveSlot(order, 3, 1)).toEqual(['a', 'd', 'b', 'c']);
  });

  it('returns a copy, never the same array', () => {
    const out = moveSlot(order, 1, 1);
    expect(out).toEqual(order);
    expect(out).not.toBe(order);
  });

  it('treats an out-of-range drop as nothing happening', () => {
    expect(moveSlot(order, 0, 9)).toEqual(order);
    expect(moveSlot(order, -1, 2)).toEqual(order);
  });
});

describe('filesForSlot / primaryFile / boardPins', () => {
  it('sorts by sort_order and breaks ties on resource_id', () => {
    const files = filesForSlot(CHARACTER_DETAIL.files, 'stills', null);
    expect(files.map((f) => f.resource_id)).toEqual([
      '727145299382534151',
      '727145299382534150',
    ]);
  });

  it('primaryFile is the first file of the type primary slot', () => {
    expect(primaryFile(CHARACTER_DETAIL, null)?.resource_id).toBe('727145299382534146');
  });

  it('primaryFile is null for prompt, which has no file primary', () => {
    expect(primaryFile(PRESET_PROMPT_DETAIL, null)).toBeNull();
  });

  it('a loadout filters worn pins to that loadout plus the unassigned ones', () => {
    const all = boardPins(CHARACTER_DETAIL, null).find((p) => p.slot === 'worn');
    expect(all?.count).toBe(3);

    const night = boardPins(CHARACTER_DETAIL, '727145299382534401').find(
      (p) => p.slot === 'worn',
    );
    expect(night?.files.map((f) => f.resource_id)).toEqual([
      '727145299382534161', // this loadout's
      '727145299382534162', // belongs to no loadout — shown under every one
    ]);

    const dflt = boardPins(CHARACTER_DETAIL, '727145299382534400').find((p) => p.slot === 'worn');
    expect(dflt?.files.map((f) => f.resource_id)).toEqual([
      '727145299382534160',
      '727145299382534162',
    ]);
  });

  it('renders empty slots as pins so the board shows the type shape', () => {
    const pins = boardPins(CHARACTER_DETAIL, null);
    expect(pins.map((p) => p.slot)).toEqual([
      'stills',
      'expressions',
      'extras',
      'worn',
      'unsorted',
    ]);
    expect(pins.find((p) => p.slot === 'expressions')?.count).toBe(0);
  });
});

describe('loadouts', () => {
  it('defaults to the flagged loadout', () => {
    expect(defaultLoadoutId(CHARACTER_DETAIL.loadouts)).toBe('727145299382534400');
  });

  it('falls back to the lowest sort_order when nothing is flagged', () => {
    expect(
      defaultLoadoutId([
        makeLoadout({ id: 'b', is_default: false, sort_order: 2 }),
        makeLoadout({ id: 'a', is_default: false, sort_order: 1 }),
      ]),
    ).toBe('a');
  });

  it('is null when there are none', () => {
    expect(defaultLoadoutId([])).toBeNull();
  });

  it('only characters have loadouts', () => {
    expect(hasLoadouts('character')).toBe(true);
    expect(hasLoadouts('costume')).toBe(false);
    expect(hasLoadouts('audio')).toBe(false);
  });
});

describe('relationSectionsFor', () => {
  it('character gets Wears and Holds, both addable', () => {
    const specs = relationSectionsFor('character', null);
    expect(specs.map((s) => s.key)).toEqual(['wears', 'holds']);
    expect(specs[0]).toMatchObject({ targetType: 'costume', addRelation: 'wears' });
    expect(specs[1]).toMatchObject({ targetType: 'prop', addRelation: 'holds' });
  });

  it('costume and prop get the incoming side, which cannot be added to here', () => {
    expect(relationSectionsFor('costume', null)).toMatchObject([
      { key: 'wornBy', direction: 'incoming', targetType: null },
    ]);
    expect(relationSectionsFor('prop', null)).toMatchObject([
      { key: 'heldBy', direction: 'incoming', targetType: null },
    ]);
  });

  it('audio gets one section whose target follows the subtype', () => {
    expect(relationSectionsFor('audio', 'sfx')[0]).toMatchObject({
      key: 'attachedTo',
      targetType: 'location',
      addRelation: 'ambience_of',
      addBlockedReason: null,
    });
    expect(relationSectionsFor('audio', 'voice')[0]).toMatchObject({
      targetType: 'character',
      addRelation: 'voice_of',
    });
  });

  it('audio with no subtype still lists links but says why it cannot gain one', () => {
    // The server would refuse with 422 `link_not_allowed`; refusing here with
    // a reason is the same answer arriving before the user picks a target.
    const spec = relationSectionsFor('audio', null)[0];
    expect(spec.targetType).toBeNull();
    expect(spec.addRelation).toBeNull();
    expect(spec.addBlockedReason).toBe('audioSubtype');
    expect(spec.relations).toEqual(['ambience_of', 'voice_of']);
  });

  it('location and prompt have no relation sections', () => {
    expect(relationSectionsFor('location', null)).toEqual([]);
    expect(relationSectionsFor('prompt', null)).toEqual([]);
  });
});

describe('audioRelationFor', () => {
  it('maps each subtype to its one relation', () => {
    expect(audioRelationFor('sfx')).toBe('ambience_of');
    expect(audioRelationFor('music')).toBe('ambience_of');
    expect(audioRelationFor('voice')).toBe('voice_of');
    expect(audioRelationFor(null)).toBeNull();
    expect(audioRelationFor('foley')).toBeNull();
  });
});

describe('linkRowsFor / relatedAssetIds', () => {
  it('outgoing sections read links, incoming ones read linked_by', () => {
    const out = linkRowsFor(CHARACTER_DETAIL, relationSectionsFor('character', null)[0]);
    expect(out.map((r) => r.otherId)).toEqual(['727145299382534310']);

    const incoming = linkRowsFor(COSTUME_DETAIL, relationSectionsFor('costume', null)[0]);
    expect(incoming.map((r) => r.otherId)).toEqual(['727145299382534300']);
  });

  it('audio picks up both of its relations in one section', () => {
    const rows = linkRowsFor(AUDIO_DETAIL, relationSectionsFor('audio', 'sfx')[0]);
    expect(rows.map((r) => r.otherId)).toEqual(['727145299382534302']);
  });

  it('collects link targets and loadout members alike', () => {
    // The loadout chips name costumes the sheet has no link row for, so both
    // sources have to be resolved or a chip renders a raw Snowflake.
    expect(relatedAssetIds(CHARACTER_DETAIL)).toEqual(['727145299382534310']);
    const withProp = makeDetail({
      id: '1',
      loadouts: [makeLoadout({ id: 'l1', prop_ids: ['999'] })],
    });
    expect(relatedAssetIds(withProp)).toEqual(['999']);
  });
});

describe('composeLoadoutPrompt', () => {
  const costume = makeRow({
    id: '727145299382534310',
    asset_type: 'costume',
    prompt_positive: 'black hooded robe',
    prompt_negative: 'shiny, satin',
  });
  const prop = makeRow({
    id: '727145299382534311',
    asset_type: 'prop',
    prompt_positive: 'lacquered dagger',
    prompt_negative: 'satin',
  });

  it('concatenates asset, loadout extra, costumes then props in that order', () => {
    const loadout = makeLoadout({
      id: 'l',
      prompt_extra: 'black hooded night robe',
      costume_ids: ['727145299382534310'],
      prop_ids: ['727145299382534311'],
    });
    expect(
      composeLoadoutPrompt(CHARACTER_DETAIL, loadout, {
        '727145299382534310': costume,
        '727145299382534311': prop,
      }),
    ).toBe(
      'same woman as reference…, black hooded night robe, black hooded robe, lacquered dagger',
    );
  });

  it('drops blank parts rather than emitting empty separators', () => {
    expect(
      composeLoadoutPrompt(CHARACTER_DETAIL, makeLoadout({ id: 'l', prompt_extra: '   ' }), {}),
    ).toBe('same woman as reference…');
  });

  it('an unresolved member contributes nothing, never "undefined"', () => {
    // A costume whose row failed to load must not land in the clipboard as
    // the literal string "undefined" in the middle of a prompt.
    const out = composeLoadoutPrompt(
      CHARACTER_DETAIL,
      makeLoadout({ id: 'l', costume_ids: ['missing'] }),
      {},
    );
    expect(out).toBe('same woman as reference…');
    expect(out).not.toContain('undefined');
  });

  it('works with no loadout at all', () => {
    expect(composeLoadoutPrompt(CHARACTER_DETAIL, null, {})).toBe('same woman as reference…');
  });

  it('negatives are the de-duplicated union', () => {
    const loadout = makeLoadout({
      id: 'l',
      costume_ids: ['727145299382534310'],
      prop_ids: ['727145299382534311'],
    });
    const asset = makeRow({ id: 'x', prompt_negative: 'blurry, shiny' });
    expect(
      composeLoadoutNegative(asset, loadout, {
        '727145299382534310': costume,
        '727145299382534311': prop,
      }),
    ).toBe('blurry, shiny, satin');
  });
});

describe('header extras', () => {
  it('reads audio loopable and duration defensively', () => {
    expect(audioLoopable(AUDIO_DETAIL.attrs)).toBe(true);
    expect(audioLoopable({ loopable: 'yes' })).toBe(false);
    expect(audioDurationSec(AUDIO_DETAIL.attrs)).toBe(92);
    expect(audioDurationSec({ duration_sec: '31.5' })).toBe(31.5);
    expect(audioDurationSec({ duration_sec: 'soon' })).toBeNull();
    expect(audioDurationSec({})).toBeNull();
  });

  it('formats a duration without ever showing :60', () => {
    expect(formatDuration(92)).toBe('1:32');
    expect(formatDuration(59.9)).toBe('0:59');
    expect(formatDuration(0)).toBe('0:00');
  });

  it('normalizes both placeholder shapes', () => {
    expect(promptPlaceholders({ placeholders: ['subject', 'style'] })).toEqual([
      { name: 'subject', hint: '' },
      { name: 'style', hint: '' },
    ]);
    expect(promptPlaceholders(PRESET_PROMPT_DETAIL.attrs)).toEqual([
      { name: 'subject', hint: 'who the sheet is of' },
    ]);
    expect(promptPlaceholders({ placeholders: 42 })).toEqual([]);
  });

  it('renders platform params, nested values included, keeping the stored value', () => {
    expect(platformParamRows({ aspect_ratio: '1:1', size: { w: 2 } })).toEqual([
      {
        key: 'aspect_ratio',
        value: '1:1',
        original: { key: 'aspect_ratio', value: '1:1' },
      },
      { key: 'size', value: '{"w":2}', original: { key: 'size', value: { w: 2 } } },
    ]);
    expect(platformParamRows(null)).toEqual([]);
  });
});

describe('platformParamsFromRows', () => {
  it('writes an untouched row back with its stored TYPE', () => {
    // The load-bearing case. A stored string "4" renders as the text `4`;
    // re-reading it would make it the NUMBER 4 - retyping a value nobody
    // touched, which is exactly what the panel exists not to do.
    const rows = platformParamRows({ model: '4', flag: 'null', shape: '{"a":1}' });
    expect(platformParamsFromRows(rows)).toEqual({
      model: '4',
      flag: 'null',
      shape: '{"a":1}',
    });
  });

  it('round-trips non-string stored values unchanged', () => {
    const rows = platformParamRows({ steps: 30, size: { w: 2 }, on: true, off: null });
    expect(platformParamsFromRows(rows)).toEqual({
      steps: 30,
      size: { w: 2 },
      on: true,
      off: null,
    });
  });

  it('re-reads a row the user actually edited', () => {
    const [row] = platformParamRows({ steps: 30 });
    expect(platformParamsFromRows([{ ...row, value: '40' }])).toEqual({ steps: 40 });
    // Text that is not JSON stays text.
    expect(platformParamsFromRows([{ ...row, value: '16:9' }])).toEqual({ steps: '16:9' });
  });

  it('a renamed key counts as an edit', () => {
    const [row] = platformParamRows({ model: '4' });
    expect(platformParamsFromRows([{ ...row, key: 'engine' }])).toEqual({ engine: 4 });
  });

  it('drops a half-typed new row', () => {
    expect(
      platformParamsFromRows([
        { key: '', value: 'orphan', original: null },
        { key: '  ', value: 'x', original: null },
      ]),
    ).toEqual({});
  });

  it('a brand new row is read from its text', () => {
    expect(platformParamsFromRows([{ key: 'steps', value: '30', original: null }])).toEqual({
      steps: 30,
    });
  });
});

describe('deepEqual', () => {
  it('ignores key order but not values', () => {
    expect(deepEqual({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(deepEqual({ a: 1 }, { a: '1' })).toBe(false);
    expect(deepEqual({ a: { b: [1, 2] } }, { a: { b: [1, 2] } })).toBe(true);
    expect(deepEqual({ a: { b: [1, 2] } }, { a: { b: [2, 1] } })).toBe(false);
  });

  it('distinguishes missing keys from undefined ones', () => {
    expect(deepEqual({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(deepEqual({}, {})).toBe(true);
  });

  it('handles null and primitives without throwing', () => {
    expect(deepEqual(null, null)).toBe(true);
    expect(deepEqual(null, {})).toBe(false);
    expect(deepEqual([1], { 0: 1 })).toBe(false);
  });
});

describe('canvasKindFor', () => {
  it('uses the matching kind where one exists and smart otherwise', () => {
    expect(canvasKindFor('character')).toBe('character');
    expect(canvasKindFor('location')).toBe('location');
    expect(canvasKindFor('prop')).toBe('prop');
    expect(canvasKindFor('costume')).toBe('smart');
    expect(canvasKindFor('prompt')).toBe('smart');
    expect(canvasKindFor('audio')).toBe('smart');
  });
});

describe('primary file honours the loadout filter too', () => {
  it('a loadout-scoped primary is hidden under another loadout', () => {
    const detail = makeDetail({
      id: '1',
      files: [makeFile({ resource_id: 'r1', slot: 'sheet', loadout_id: 'l2' })],
    });
    expect(primaryFile(detail, 'l2')?.resource_id).toBe('r1');
    expect(primaryFile(detail, 'l1')).toBeNull();
  });
});
