// features/canvas-core/library/librarySelection.ts
//
// Multi-select arithmetic for the library grid, kept OUT of the component so
// the four gestures can be pinned without a DOM. A selection is a list of
// `${store}:${id}` keys plus an anchor index; the keys survive a re-query that
// changes the list under them, and `selectedItems` drops the ones that no
// longer resolve rather than inventing rows for them.

import type { LibraryItem, LibraryStore } from './librarySearch';

export type LibraryItemKey = string;

export function libraryKey(item: { store: LibraryStore; id: string }): LibraryItemKey {
  return `${item.store}:${item.id}`;
}

export interface PickModifiers {
  meta: boolean;
  shift: boolean;
}

export interface SelectionState {
  keys: LibraryItemKey[];
  /** Where a shift range starts. Null before the first click. */
  anchor: number | null;
}

export function applyPick(
  state: SelectionState,
  items: readonly LibraryItem[],
  index: number,
  mods: PickModifiers,
): SelectionState {
  const item = items[index];
  if (!item) return state;
  const key = libraryKey(item);

  if (mods.shift) {
    // The anchor SURVIVES a range pick: a second shift-click must re-range from
    // the same origin, not walk the selection along one row at a time.
    const anchor = state.anchor ?? index;
    const lo = Math.min(anchor, index);
    const hi = Math.max(anchor, index);
    return {
      keys: items.slice(lo, hi + 1).map(libraryKey),
      anchor,
    };
  }

  if (mods.meta) {
    const has = state.keys.includes(key);
    return {
      keys: has ? state.keys.filter((k) => k !== key) : [...state.keys, key],
      anchor: index,
    };
  }

  return { keys: [key], anchor: index };
}

/** Keys back to rows, IN LIST ORDER — which is delivery order for everything
 *  downstream (references ship in the order they are added). A key with no row
 *  is dropped: the list was re-queried and that item is simply not here. */
export function selectedItems(
  keys: readonly LibraryItemKey[],
  items: readonly LibraryItem[],
): LibraryItem[] {
  const wanted = new Set(keys);
  return items.filter((i) => wanted.has(libraryKey(i)));
}
