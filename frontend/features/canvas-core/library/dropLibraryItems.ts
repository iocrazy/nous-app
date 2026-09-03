// features/canvas-core/library/dropLibraryItems.ts
//
// One custom MIME carries a whole multi-item library drag, and one dispatcher
// decides what a landing means. Both live here rather than in CanvasSurface so
// the surface's job stays "convert a DOM event into a target" — the file is at
// its size budget and this is real logic, not wiring.
//
// The payload is a JSON string in ONE slot. `dataTransfer` only carries
// strings, and spreading a multi-item drag over several keys would make the
// reader reassemble it — with no way to tell a partial write from a foreign
// drag. `text/plain` is written alongside it purely so dropping into a text
// field yields the titles instead of nothing.

import { resolveReferenceRefs, addReferences } from './addReferences';
import type { GeneratedImageRef, MediaNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { LibraryItem, LibraryStore } from './librarySearch';
import { placeLibraryItems } from './placeLibraryItems';

export const LIBRARY_DND_MIME = 'application/x-nous-library';

/** The wire form. Deliberately a JSON string in one MIME slot: dataTransfer
 *  carries strings, and one slot keeps the reader from having to reassemble
 *  a multi-item drag out of several keys. */
export interface LibraryDragPayload {
  items: Array<{ store: LibraryStore; id: string; kind: string; title: string }>;
}

export function writeLibraryDrag(dt: DataTransfer, items: readonly LibraryItem[]): void {
  const payload: LibraryDragPayload = {
    items: items.map((i) => ({ store: i.store, id: i.id, kind: i.kind, title: i.title })),
  };
  dt.setData(LIBRARY_DND_MIME, JSON.stringify(payload));
  dt.setData('text/plain', items.map((i) => i.title).join(', '));
}

export function hasLibraryDrag(dt: { types: readonly string[] | DOMStringList }): boolean {
  return Array.from(dt.types ?? []).includes(LIBRARY_DND_MIME);
}

export function readLibraryDrag(dt: DataTransfer): LibraryItem[] | null {
  if (!hasLibraryDrag(dt)) return null;
  try {
    const parsed = JSON.parse(dt.getData(LIBRARY_DND_MIME)) as LibraryDragPayload;
    if (!Array.isArray(parsed?.items)) return null;
    return parsed.items.map((i) => ({
      store: i.store,
      id: String(i.id),
      title: i.title,
      kind: i.kind,
      // A dropped item is never RENDERED — the three landings place a node,
      // add a reference or append to a card, and each mints its own durable
      // url from the id. Carrying a thumbnail across the wire would mean
      // guessing an endpoint per store, and a generation's cover does not
      // live where a resource's does. Empty is the honest answer.
      thumbUrl: '',
    }));
  } catch (err) {
    // A malformed payload must not throw INSIDE a drop handler: the browser
    // would leave the drag visually stuck with no error anyone can see.
    console.error('[dropLibraryItems] unreadable drag payload:', err);
    return null;
  }
}

export type LibraryDropTarget =
  | { kind: 'canvas'; position: { x: number; y: number } }
  | { kind: 'prompt'; nodeId: string; mention: boolean }
  | { kind: 'media'; nodeId: string };

export interface LibraryDropOutcome {
  handled: boolean;
  placed: number;
  referenced: number;
  mentioned: number;
  failed: number;
  /** Resolved fine, but was already where it was dropped. NOT a failure, and
   *  not a success either — a drop that skipped everything changes nothing on
   *  screen, so a caller that cannot see this number has no way to tell it
   *  apart from one that worked. */
  skipped: number;
  /** Resolved fine, refused by the target model's reference ceiling. Only the
   *  prompt landing can produce it. */
  clamped: number;
}

export interface DropLibraryOptions {
  /** The target model's reference ceiling, when the CALLER knows it. Only a
   *  node view can — the ceiling comes from `useModelCapabilities`, and a
   *  module that called a hook would be one this cannot be called from a
   *  drop handler. Omitted means no ceiling, exactly as `addReferences`
   *  reads it. */
  maxRefs?: number;
}

// Frozen: it is handed OUT, and a caller that mutated the result would corrupt
// every later empty drop.
const NOTHING: LibraryDropOutcome = Object.freeze({
  handled: false, placed: 0, referenced: 0, mentioned: 0, failed: 0, skipped: 0, clamped: 0,
});

/** The label a hovered node shows, so a drop never resolves into a surprise. */
export function dropConsequenceKey(target: LibraryDropTarget): string {
  if (target.kind === 'prompt') {
    return target.mention
      ? 'canvas.library.dropInsertMention'
      : 'canvas.library.dropAddReference';
  }
  if (target.kind === 'media') return 'canvas.library.dropAppend';
  return 'canvas.library.dropPlace';
}

export async function dropLibraryItems(
  items: readonly LibraryItem[],
  target: LibraryDropTarget,
  scopeId: string,
  opts?: DropLibraryOptions,
): Promise<LibraryDropOutcome> {
  if (items.length === 0) return NOTHING;

  if (target.kind === 'canvas') {
    const r = await placeLibraryItems(items, scopeId, target.position);
    return {
      handled: true, placed: r.inserted, referenced: 0, mentioned: 0,
      failed: r.failed.length, skipped: r.skipped, clamped: 0,
    };
  }

  if (target.kind === 'prompt') {
    // ⌥ means "mention", and a mention is an edit to the prompt DOCUMENT —
    // only the node holds the editor handle that can make it. This function
    // reports the routing decision and lets the node do the insert.
    if (target.mention) {
      return { ...NOTHING, handled: true };
    }
    // Forwarded only when the caller supplied one: `addReferences` reads a
    // MISSING options object as "no ceiling", and passing `undefined`
    // explicitly would say the same thing in a shape callers cannot assert on.
    const r =
      opts?.maxRefs === undefined
        ? await addReferences(target.nodeId, items, scopeId)
        : await addReferences(target.nodeId, items, scopeId, { maxRefs: opts.maxRefs });
    return {
      handled: true, placed: 0, referenced: r.added, mentioned: 0,
      failed: r.failed.length, skipped: r.skipped, clamped: r.clamped,
    };
  }

  // media — append to that card, exactly as a file drop on it would.
  // `allowVideo` is ON: a media CARD holds image|video, which is the same rule
  // `placeLibraryItems` applies when it builds one from scratch.
  const refs: GeneratedImageRef[] = [];
  let failed = 0;
  for (const item of items) {
    try {
      refs.push(...(await resolveReferenceRefs(item, scopeId, { allowVideo: true })));
    } catch (err) {
      console.error('[dropLibraryItems] could not resolve for a media card:', err);
      failed += 1;
    }
  }
  if (refs.length === 0) return { ...NOTHING, handled: true, failed };
  const store = useCanvasCoreStore.getState();
  // The LIVE node, re-read after the awaits.
  const current = ((store.nodes.find((n) => (n as { id?: unknown }).id === target.nodeId) as
    | { data?: MediaNodeData }
    | undefined)?.data?.items ?? []) as GeneratedImageRef[];
  const have = new Set(current.map((r) => r.url));
  const fresh = refs.filter((r) => !have.has(r.url));
  if (fresh.length > 0) {
    store.patchNode(target.nodeId, { data: { items: [...current, ...fresh] } });
  }
  return {
    handled: true, placed: fresh.length, referenced: 0, mentioned: 0, failed,
    // Everything the dedupe above took away: the card already held it.
    skipped: refs.length - fresh.length, clamped: 0,
  };
}
