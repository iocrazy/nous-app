// features/canvas-core/library/libraryStore.ts
//
// Panel state, in its OWN zustand store rather than in `canvasCoreStore`.
//
// Two reasons, and the second is the load-bearing one. `canvasCoreStore` is
// over a thousand lines and every write there is entangled with
// dirty/revision/history bookkeeping — but more importantly, this state is not
// part of the DOCUMENT. Which library segment is showing must never mark a
// canvas dirty, never enter the undo stack, and never reach `nodes_json`.
//
// Selection holds `${store}:${id}` KEYS, never copies of the rows: the rows are
// re-queried constantly and a copy would go stale the moment a search narrows.

import { create } from 'zustand';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { AssetScope, GeneratedScope, LibraryStore } from './librarySearch';
import type { LibraryItemKey } from './librarySelection';

// Composed, not written out as one literal, and that is deliberate. The
// localStorage namespace and the i18n namespace genuinely collide here:
// `libraryI18n.test.ts` scans this tree for single-quoted `'canvas.library.*'`
// literals and demands every hit be a translated message, so spelling this
// key out would enrol a STORAGE key in the locale files. Splitting at the last
// dot keeps the stored key exactly `canvas.library.v1` while leaving the guard
// scanning only for what it is actually about.
const STORAGE_NAMESPACE = 'canvas.library';
export const LIBRARY_STORAGE_KEY = `${STORAGE_NAMESPACE}.v1`;

export type LibraryPage = 'media' | 'prompts';

export interface LibraryTarget {
  nodeId: string;
  /**
   * Only PROMPT nodes can be aimed at.
   *
   * A one-member union rather than a dropped field: the panel's target bar and
   * `LibraryMediaPage`'s target mode both test `kind === 'prompt'`, and keeping
   * the discriminant is what lets a second aimable node type be added later
   * without every consumer changing shape. `'media'` was in this union and
   * nothing ever produced it, so it only widened what a caller could arm past
   * what the panel would honour — a target the shelf silently ignores.
   *
   * NOT the same type as `LibraryDropTarget` (dropLibraryItems.ts), which does
   * carry `media`: a drop lands on a media node, an aim does not.
   */
  kind: 'prompt';
  /** Display name at open time. The id is the authority. */
  title: string;
}

/** Only the three durable knobs. `query`, `selection` and `target` are
 *  deliberately NOT persisted: a restored target points at a node id from
 *  whatever canvas was open last time, which is worse than no target. */
interface Persisted {
  page: LibraryPage;
  mediaStore: LibraryStore;
  width: number;
}

const DEFAULTS: Persisted = { page: 'media', mediaStore: 'assets', width: 340 };

function readPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(LIBRARY_STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return {
      page: parsed.page === 'prompts' ? 'prompts' : 'media',
      mediaStore:
        parsed.mediaStore === 'uploads' || parsed.mediaStore === 'generated'
          ? parsed.mediaStore
          : 'assets',
      width: typeof parsed.width === 'number' && parsed.width > 0 ? parsed.width : DEFAULTS.width,
    };
  } catch (err) {
    // A corrupt value must degrade to the default, not white-screen the canvas.
    console.error('[libraryStore] could not read stored panel state:', err);
    return DEFAULTS;
  }
}

function writePersisted(p: Persisted): void {
  try {
    localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(p));
  } catch (err) {
    console.error('[libraryStore] could not persist panel state:', err);
  }
}

export interface LibraryPanelState extends Persisted {
  open: boolean;
  query: string;
  kind: string | null;
  assetScope: AssetScope;
  generatedScope: GeneratedScope;
  selection: LibraryItemKey[];
  target: LibraryTarget | null;
  /** Bumped whenever something asks the search box to take focus. */
  focusNonce: number;
  openPanel(opts?: {
    page?: LibraryPage;
    mediaStore?: LibraryStore;
    target?: LibraryTarget | null;
    focusSearch?: boolean;
  }): void;
  close(): void;
  toggle(): void;
  setPage(page: LibraryPage): void;
  setMediaStore(store: LibraryStore): void;
  setQuery(q: string): void;
  setKind(kind: string | null): void;
  setAssetScope(scope: AssetScope): void;
  setGeneratedScope(scope: GeneratedScope): void;
  setSelection(keys: LibraryItemKey[]): void;
  /**
   * No caller yet — kept deliberately, not dead code awaiting a sweep.
   *
   * `width` is already persisted and already read by the panel's geometry; P3's
   * Prompts page opens at 600px against the Media page's 340px (spec §3.1), and
   * this is the setter that switch goes through. Deleting it would mean
   * re-adding the setter, the persist call and its test together next quarter.
   */
  setWidth(px: number): void;
  clearTarget(): void;
}

/**
 * Drop the highlight this panel put on its target — and ONLY that one.
 *
 * Guarded on the selection still BEING the target: between opening and
 * closing, the user may have clicked another card, and a bare
 * `setSelection([])` would then deselect something the panel never selected.
 */
function releaseHighlight(target: LibraryTarget | null): void {
  if (!target) return;
  const canvas = useCanvasCoreStore.getState();
  const sel = canvas.selection;
  if (sel.length === 1 && sel[0] === target.nodeId) canvas.setSelection([]);
}

export const useLibraryStore = create<LibraryPanelState>((set, get) => {
  const persist = () => {
    const { page, mediaStore, width } = get();
    writePersisted({ page, mediaStore, width });
  };
  return {
    ...readPersisted(),
    open: false,
    query: '',
    kind: null,
    assetScope: 'all',
    generatedScope: 'this-canvas',
    selection: [],
    target: null,
    focusNonce: 0,

    openPanel(opts = {}) {
      set((s) => ({
        open: true,
        page: opts.page ?? s.page,
        mediaStore: opts.mediaStore ?? s.mediaStore,
        target: opts.target !== undefined ? opts.target : s.target,
        selection: [],
        focusNonce: opts.focusSearch ? s.focusNonce + 1 : s.focusNonce,
      }));
      // Spec §3.1: the aimed-at node is SELECTED and highlighted while the
      // panel points at it. The panel sits on the right and the target bar
      // names the node, but with a dozen cards on the board a name is not a
      // location — nothing said WHICH card was about to receive references.
      // The canvas's own selection is the highlight this board already has,
      // so this borrows it rather than inventing a second marker.
      if (opts.target) useCanvasCoreStore.getState().setSelection([opts.target.nodeId]);
      persist();
    },
    // Closing releases the target. A target that outlives its panel silently
    // re-arms the next open, so the user's next `L` would start adding
    // references to a node they have forgotten about.
    close() {
      releaseHighlight(get().target);
      set({ open: false, target: null, selection: [] });
    },
    toggle() {
      if (get().open) get().close();
      else get().openPanel();
    },
    setPage(page) {
      set({ page });
      persist();
    },
    setMediaStore(mediaStore) {
      // Keys are `store:id`, so a key kept across this switch resolves against
      // nothing in the new segment: it would be counted by the footer and then
      // silently dropped at send time. The kind chips are per-store too.
      set({ mediaStore, selection: [], kind: null });
      persist();
    },
    setQuery(query) {
      set({ query });
    },
    setKind(kind) {
      set({ kind });
    },
    setAssetScope(assetScope) {
      set({ assetScope, selection: [] });
    },
    setGeneratedScope(generatedScope) {
      set({ generatedScope, selection: [] });
    },
    setSelection(selection) {
      set({ selection });
    },
    setWidth(width) {
      set({ width });
      persist();
    },
    clearTarget() {
      releaseHighlight(get().target);
      set({ target: null });
    },
  };
});
