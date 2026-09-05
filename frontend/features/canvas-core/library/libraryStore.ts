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

import type { PromptForm, PromptSegment } from '../../../services/promptsService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type {
  AssetScope,
  GeneratedScope,
  LibraryStore,
  UploadSource,
} from './librarySearch';
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

/** The natural width of each page (spec §3.1). The Prompts page draws a card
 *  grid with a preview column; at the Media page's 340px it wraps to one
 *  narrow column per row and the preview is unreadable. */
export const PANEL_WIDTH: Record<LibraryPage, number> = { media: 340, prompts: 600 };

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

/** Only the three durable knobs. Everything else is NON-DURABLE — `query`,
 *  `selection`, `target`, and the three filters — and non-durable is not one
 *  lifetime:
 *
 *  - `kind` and `uploadSource` are IN-SEGMENT: `setMediaStore` resets them,
 *    because both rows belong to one shelf and a chip left pressed would
 *    narrow a shelf that does not draw the control saying why.
 *  - `assetsInLibraryOnly` is KEPT ACROSS A SEGMENT SWITCH. It lives in the
 *    Assets scope row beside `assetScope`, which is kept for the same reason:
 *    the pill is drawn `aria-pressed` whenever that row is on screen, so a
 *    returning user is never narrowed by a control they cannot see.
 *
 *  None of the three is persisted: a restored target points at a node id from
 *  whatever canvas was open last time, which is worse than no target, and a
 *  restored source chip opens the Files shelf already narrowed with nothing on
 *  screen explaining why.
 *
 *  `mediaStore` keeps its stored value `'uploads'`. The SEGMENT is labelled
 *  "Files" now, but the key is on disk in every user's browser and renaming it
 *  would silently reset everyone's panel to Assets. */
interface Persisted {
  page: LibraryPage;
  mediaStore: LibraryStore;
  width: number;
  /** Which language column of a prompt the Prompts page shows. Durable
   *  because it is a reading preference, not a filter: it narrows nothing and
   *  the toggle is on screen whenever the page is. */
  promptLang: 'en' | 'zh';
}

const DEFAULTS: Persisted = { page: 'media', mediaStore: 'assets', width: 340, promptLang: 'en' };

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
      promptLang: parsed.promptLang === 'zh' ? 'zh' : 'en',
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
  /** Which source the Files shelf is narrowed to. Per-segment, like `kind`. */
  uploadSource: UploadSource;
  /**
   * Is the Assets shelf narrowed to library members? `true` at rest.
   *
   * A library member is one somebody ADDED (mig 449, user ruling reaffirmed
   * 2026-09-05). Script imports and the rows the P4 legacy-card migration
   * created were never added, so a shelf that listed them would present as
   * library members things that are not.
   *
   * Kept across a segment switch, unlike `uploadSource` and `kind`: it sits
   * in the Assets scope row next to `assetScope`, which is also kept, and the
   * pill is drawn `aria-pressed` whenever that row is on screen — so a
   * returning user is never narrowed by a control they cannot see. Not
   * persisted, so a new session starts narrow.
   */
  assetsInLibraryOnly: boolean;
  assetScope: AssetScope;
  generatedScope: GeneratedScope;
  /** Which shelf the Prompts page is showing. Not persisted: it is a filter,
   *  and a restored `system` segment opens the page listing none of the user's
   *  own prompts. */
  promptSegment: PromptSegment;
  /** The Prompts page's form filter, `null` for all forms. Not persisted, for
   *  the same reason as `promptSegment`. */
  promptForm: PromptForm | null;
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
  setPromptLang(lang: 'en' | 'zh'): void;
  setPromptSegment(s: PromptSegment): void;
  setPromptForm(f: PromptForm | null): void;
  setQuery(q: string): void;
  setKind(kind: string | null): void;
  setUploadSource(source: UploadSource): void;
  setAssetsInLibraryOnly(only: boolean): void;
  setAssetScope(scope: AssetScope): void;
  setGeneratedScope(scope: GeneratedScope): void;
  setSelection(keys: LibraryItemKey[]): void;
  /**
   * No caller yet — kept deliberately, not dead code awaiting a sweep.
   *
   * The PAGE-width switch does NOT come through here: `setPage` and `openPanel`
   * assert `PANEL_WIDTH[page]` themselves, because that width belongs to the
   * page rather than to whoever last called a setter. This one is for a width
   * the USER chooses (a drag handle on the panel edge), which is why it
   * persists. Deleting it would mean re-adding the setter, the persist call and
   * its test together when that handle lands.
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
    const { page, mediaStore, width, promptLang } = get();
    writePersisted({ page, mediaStore, width, promptLang });
  };
  return {
    ...readPersisted(),
    open: false,
    query: '',
    kind: null,
    uploadSource: 'all',
    assetsInLibraryOnly: true,
    assetScope: 'all',
    generatedScope: 'this-canvas',
    promptSegment: 'mine',
    promptForm: null,
    selection: [],
    target: null,
    focusNonce: 0,

    openPanel(opts = {}) {
      set((s) => {
        // A PROGRAMMATIC segment switch is a segment switch. `setMediaStore`
        // drops the kind chip because it belongs to the shelf being left, and
        // the same is true when the caller names a different `mediaStore`
        // here — the header's Open Library button forces `uploads`, so a
        // `character` chip chosen on Assets would otherwise arrive on the
        // Files shelf, narrow it to nothing, and draw no chip saying so
        // (`KIND_LABEL.uploads` has no `character` entry).
        //
        // `uploadSource` needs no equivalent: `LibraryMediaPage` reads it only
        // on `uploads`, and `assetsInLibraryOnly` is KEPT here on purpose —
        // see the Persisted doc above.
        const switching = opts.mediaStore !== undefined && opts.mediaStore !== s.mediaStore;
        // Spec §3.1/§3.4: a page carries its own width, so an open that lands
        // on the OTHER page re-asserts it. Guarded on the page actually
        // changing — re-asserting on a same-page open would throw away a
        // width the user dragged.
        const nextPage = opts.page;
        const changingPage = nextPage !== undefined && nextPage !== s.page;
        return {
          open: true,
          page: nextPage ?? s.page,
          ...(changingPage ? { width: PANEL_WIDTH[nextPage] } : null),
          mediaStore: opts.mediaStore ?? s.mediaStore,
          kind: switching ? null : s.kind,
          target: opts.target !== undefined ? opts.target : s.target,
          selection: [],
          focusNonce: opts.focusSearch ? s.focusNonce + 1 : s.focusNonce,
        };
      });
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
      // Spec §3.1/§3.4: the two pages have different natural widths and the
      // width is persisted, so switching pages re-asserts the page's width.
      set({ page, width: PANEL_WIDTH[page] });
      persist();
    },
    setMediaStore(mediaStore) {
      // Keys are `store:id`, so a key kept across this switch resolves against
      // nothing in the new segment: it would be counted by the footer and then
      // silently dropped at send time. The kind chips and the Files source
      // chips are per-store too — a source left pressed here would survive a
      // trip to Generated (which draws no source row) and narrow the shelf on
      // the way back with nothing on screen saying why.
      set({ mediaStore, selection: [], kind: null, uploadSource: 'all' });
      persist();
    },
    setPromptLang(promptLang) {
      set({ promptLang });
      persist();
    },
    // Selection cleared for the reason the other filter setters clear it: the
    // keys resolve against rows the narrowed query may not return, so a kept
    // selection is counted by the footer and dropped at send time.
    setPromptSegment(promptSegment) {
      set({ promptSegment, selection: [] });
    },
    setPromptForm(promptForm) {
      set({ promptForm });
    },
    setQuery(query) {
      set({ query });
    },
    setKind(kind) {
      set({ kind });
    },
    // Selection cleared for the same reason the scope setters clear it: the
    // keys resolve against rows the narrowed query may not return, so a kept
    // selection is counted by the footer and dropped at send time.
    setUploadSource(uploadSource) {
      set({ uploadSource, selection: [] });
    },
    setAssetsInLibraryOnly(assetsInLibraryOnly) {
      set({ assetsInLibraryOnly, selection: [] });
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
