import { describe, expect, it } from 'vitest';

import { applyPick, libraryKey, selectedItems } from './librarySelection';
import type { LibraryItem } from './librarySearch';

const items: LibraryItem[] = ['a', 'b', 'c', 'd'].map((n, i) => ({
  store: 'uploads',
  id: `65500000000000000${i}`,
  title: n,
  thumbUrl: '',
  kind: 'image',
}));
const key = (i: number) => libraryKey(items[i]);
const EMPTY = { keys: [] as string[], anchor: null };

describe('applyPick', () => {
  it('a plain click replaces the whole selection', () => {
    const first = applyPick(EMPTY, items, 2, { meta: false, shift: false });
    expect(first).toEqual({ keys: [key(2)], anchor: 2 });
    const second = applyPick(first, items, 0, { meta: false, shift: false });
    expect(second).toEqual({ keys: [key(0)], anchor: 0 });
  });

  it('meta-click toggles one without disturbing the rest', () => {
    const one = applyPick(EMPTY, items, 1, { meta: false, shift: false });
    const two = applyPick(one, items, 3, { meta: true, shift: false });
    expect(two.keys).toEqual([key(1), key(3)]);
    const back = applyPick(two, items, 1, { meta: true, shift: false });
    expect(back.keys).toEqual([key(3)]);
    expect(back.anchor).toBe(1);
  });

  it('shift-click takes the inclusive range from the anchor, in either direction', () => {
    const anchored = applyPick(EMPTY, items, 3, { meta: false, shift: false });
    const ranged = applyPick(anchored, items, 1, { meta: false, shift: true });
    expect(ranged.keys).toEqual([key(1), key(2), key(3)]);
    // The anchor SURVIVES, so a second shift-click re-ranges from the same
    // origin instead of walking the selection along.
    expect(ranged.anchor).toBe(3);
    const wider = applyPick(ranged, items, 0, { meta: false, shift: true });
    expect(wider.keys).toEqual([key(0), key(1), key(2), key(3)]);
  });

  it('shift with no anchor yet selects just the clicked row', () => {
    expect(applyPick(EMPTY, items, 2, { meta: false, shift: true })).toEqual({
      keys: [key(2)],
      anchor: 2,
    });
  });

  it('selectedItems resolves keys back to rows, in list order, dropping stale keys', () => {
    expect(selectedItems([key(3), key(1), 'uploads:gone'], items).map((i) => i.title)).toEqual([
      'b',
      'd',
    ]);
  });
});
