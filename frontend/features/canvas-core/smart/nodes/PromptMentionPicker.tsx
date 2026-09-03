/**
 * The `@` picker on a canvas prompt node.
 *
 * Replaces the tall whole-resource-library list that used to open here. That
 * list answered "which file in my library?", which is not the question someone
 * typing `@` in a prompt is asking — they mean "which of the pictures already
 * feeding this node?", "which entity from my asset library?" or "which picture
 * out of everything I have?". So there are four groups and every one of them
 * is a THUMBNAIL GRID: a picture is recognised by looking at it, and a
 * filename column made the user read instead of look.
 *
 * ─ What a pick does ─────────────────────────────────────────────────────────
 *
 * Three different outcomes behind four identically-shaped grids, which is why
 * the consequence line at the top is fixed copy rather than a nicety.
 *
 *   Input Images  inserts an `@Image N` chip and marks that picture as the
 *                 node's i2i source. No new state — the picture was already an
 *                 input.
 *   Assets        inserts an ASSET chip. No node is created: the mention IS
 *                 the reference, and at run time it is bundled exactly like a
 *                 wired asset card (see `mentionedAssets.ts`).
 *   Uploads       adds a `manual_refs` entry — the picture lands on the node's
 *   Generated     REFERENCE STRIP, not in the sentence. The caller does the
 *                 writing (`addReferences`); this component only reports which
 *                 item was chosen.
 *
 * ─ Keyboard ────────────────────────────────────────────────────────────────
 *
 * The editor keeps focus the whole time (every pick is bound to `mousedown`
 * with `preventDefault`, or the editor's blur would close this popover before
 * a click resolved). So the arrow keys, Enter and `⇥` arrive at the EDITOR,
 * and it forwards them here through the imperative handle. The alternative —
 * a focusable list — would take focus off the text the user is mid-sentence
 * in. The root's own `onKeyDown` therefore covers only the cases where focus
 * really is inside the popover, such as the library grid's search box.
 *
 * `nodrag`/`nowheel` are load-bearing, not decoration: React Flow listens for
 * mousedown on the node and stops propagation, so without them every row
 * silently does nothing for a real user while a synthetic dispatch in a test
 * still "works".
 *
 * ─ Two grids, and why they are not one ─────────────────────────────────────
 *
 * `LibraryGrid` (Uploads / Generated) and `AssetGridPicker` (Assets) look
 * alike and answer different questions, so they stay separate:
 *
 *   LibraryGrid       renders `LibraryItem` — a flattened `{id, title,
 *                     thumbUrl, kind}` projection over three stores, indexed by
 *                     `useLibrarySearch`, which is SCOPE-BOUND by construction
 *                     (no `scopeId`, no query) and multi-select.
 *   AssetGridPicker   renders asset ENTITIES, and is shared with the chat `@`
 *                     picker, which has no scope at all: chat authorizes by
 *                     team membership (P5 ruling B/G) and needs `asset_type` /
 *                     `cover_file_id` / `scope_id` off the row to stage a chip.
 *                     `LibraryItem` drops all three.
 *
 * That is the same split #2102 already drew on the DATA side, one layer up:
 * Assets deliberately do not go through `useLibrarySearch` because the
 * in-library toggle is a picker-only knob. This file keeps the popover chrome,
 * the tab strip, the Input Images grid, the footer — and every
 * `canvas.mention.*` string, which it hands to the shared grid as `labels`.
 * The strings stay HERE because `promptMentionI18n.test.ts` reads the used-key
 * list out of this source: a `t()` call that moved into the shared component
 * would look, to that guard, like a key nothing asks for any more.
 *
 * The Assets grid owns its own search box, so the `search` above is shared by
 * the other three groups only.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';

import {
  AssetGridPicker,
  ASSET_GRID_LIMIT,
  type AssetGridPickerHandle,
  type AssetGridQuery,
} from '../../../../components/assets/AssetGridPicker';
import {
  searchAssets,
  type AssetSummary,
} from '../../../../services/assetsService';
import type { AddReferencesResult } from '../../library/addReferences';
import { LibraryGrid } from '../../library/LibraryGrid';
import { useLibrarySearch, type LibraryItem } from '../../library/librarySearch';
import { libraryKey } from '../../library/librarySelection';
import { mediaSrc } from '../mediaUrl';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

/** IC caps the candidate grid; the same ceiling applies to every tab. Aliased
 *  rather than re-typed so the images and the shared asset grid cannot drift
 *  onto two different ceilings. */
export const MENTION_CANDIDATE_LIMIT = ASSET_GRID_LIMIT;

/** Shared empty list, so a group with no rows keeps a stable identity. */
const NO_ROWS: LibraryItem[] = [];

export interface MentionInputImage {
  url: string;
  /** `Image 1`, `Image 2` … — the alias the chip and the prompt text carry. */
  label: string;
}

export interface PromptMentionPickerHandle {
  /** Move the active row, wrapping. Driven by the editor's arrow keys. */
  move: (delta: number) => void;
  /** Insert the active row. Returns false when there is nothing to insert —
   *  the caller then lets Enter reach the text, rather than swallowing it. */
  commitActive: () => boolean;
  /** Advance to the next group, wrapping. Driven by the editor's `Tab`: the
   *  editor owns focus the whole time this popover is open, so a `⇥` a REAL
   *  user presses never reaches the root's own key handler. */
  cycleTab: () => void;
}

interface Props {
  /** `teams.id` for the assets calls. '' means the route has no team segment,
   *  which is a 403 rather than an unscoped query — the tab says so. */
  scopeId: string;
  inputImages: MentionInputImage[];
  onPickImage: (image: MentionInputImage, index: number) => void;
  onPickAsset: (asset: AssetSummary) => void;
  /**
   * A library picture: it becomes a REFERENCE on the node, not a body chip.
   *
   * Answering the typed `AddReferencesResult` is what lets this picker say why
   * a pick changed nothing. A caller that returns void gets the old behaviour —
   * the pick is assumed to have landed — which is only honest for a caller that
   * cannot fail.
   *
   * The caller also owns the model's reference ceiling: one item can resolve to
   * SEVERAL refs (an asset contributes one per primary-slot file), so bounding
   * picks here would not bound refs. Past the ceiling the backend drops the
   * tail and reports it as `dropped_refs` only after the run.
   */
  onPickLibraryImage: (item: LibraryItem) => void | Promise<AddReferencesResult>;
  /** The live `@query` the editor reports, which seeds the search box. */
  query: string;
  /** Canvas id for the `Generated · this canvas` group. null disables that
   *  group — a canvas opened without one has no generations to scope to, and
   *  asking anyway would list the whole workspace under a label that promises
   *  this board. */
  canvasId: string | null;
}

type Tab = 'input' | 'assets' | 'uploads' | 'generated';

/** Group order, exported so `⇥` and the tab strip cannot disagree. */
export const MENTION_TABS: readonly Tab[] = ['input', 'assets', 'uploads', 'generated'];

/** Label per group. Uploads and Generated reuse the panel's own store names —
 *  the same shelf under two entry points must not acquire two names. */
const TAB_LABEL: Record<Tab, readonly [string, string]> = {
  input: ['canvas.mention.tabInput', 'Input Images'],
  assets: ['canvas.mention.tabAssets', 'Assets'],
  uploads: ['canvas.library.storeUploads', 'Uploads'],
  generated: ['canvas.library.storeGenerated', 'Generated'],
};

export const PromptMentionPicker = forwardRef<PromptMentionPickerHandle, Props>(
  function PromptMentionPicker(
    {
      scopeId,
      inputImages,
      onPickImage,
      onPickAsset,
      onPickLibraryImage,
      query,
      canvasId,
    },
    ref,
  ) {
    const { t } = useTranslation();

    // Open on the tab that has something to show. Decided ONCE, on mount: the
    // popover is remounted every time it opens, and re-deciding mid-session
    // would yank the tab out from under a user whose upstream just changed.
    const [tab, setTab] = useState<Tab>(() =>
      inputImages.length > 0 ? 'input' : 'assets',
    );

    // The search box is the single source of the query. The editor's `@query`
    // seeds it during RENDER rather than in an effect: an effect that mirrors
    // a prop into state re-runs after the user has already typed here and
    // swallows the edit (the useEffect-prop-seed trap). Comparing against the
    // previous PROP value means typing in the box never triggers a resync.
    const [search, setSearch] = useState(query);
    const lastQueryRef = useRef(query);
    if (query !== lastQueryRef.current) {
      lastQueryRef.current = query;
      setSearch(query);
    }

    const [debounced, setDebounced] = useState(search);
    useEffect(() => {
      const timer = setTimeout(() => setDebounced(search), DEBOUNCE_MS);
      return () => clearTimeout(timer);
    }, [search]);

    // Uploads and Generated come from the shared index. Assets deliberately do
    // NOT: this picker has an in-library toggle the panel has no equivalent
    // for, and routing it through the shared hook would either lose the toggle
    // or push a picker-only knob into the shared hook.
    //
    // `stores` drops 'generated' when there is no canvas id, rather than
    // blanking the rows afterwards: a disabled store is never fetched at all,
    // so a scope-wide request is not paid for and then thrown away.
    const library = useLibrarySearch(debounced, {
      scopeId,
      stores: canvasId ? ['uploads', 'generated'] : ['uploads'],
      uploadKinds: 'image',
      canvasId,
      generatedScope: 'this-canvas',
    });

    /** The Assets grid's own state — the type chip, the in-library toggle, its
     *  rows and its highlight — lives inside the shared component. This file
     *  keeps only the handle it drives from the editor's keys, and the count
     *  the footer prints. */
    const assetsRef = useRef<AssetGridPickerHandle | null>(null);
    const [assetCount, setAssetCount] = useState(0);

    /** Why the last library pick changed nothing. null while nothing is wrong. */
    const [notice, setNotice] = useState<string | null>(null);

    /** The canvas's asset transport: one scope, plus the presets the backend
     *  unions in. The signal is accepted and dropped — `searchAssets` predates
     *  it, and the grid discards a superseded response either way. */
    const fetchAssets = useCallback(
      (params: AssetGridQuery): Promise<AssetSummary[]> =>
        searchAssets(scopeId, {
          q: params.q,
          type: params.type ?? undefined,
          limit: params.limit,
          library: params.library,
        }),
      [scopeId],
    );

    const assetLabels = useMemo(
      () => ({
        searchLabel: t('canvas.mention.searchLabel', 'Search assets'),
        searchPlaceholder: t('canvas.mention.searchPlaceholder', 'Search assets…'),
        allTypes: t('canvas.mention.allTypes', 'All'),
        loading: t('canvas.mention.loading', 'Loading…'),
        empty: t('canvas.mention.noResults', 'No assets found'),
        error: t('canvas.mention.loadFailed', 'Could not load the asset library'),
        preview: t('canvas.mention.preview', 'Preview'),
        previewGroup: t('canvas.mention.tabAssets', 'Assets'),
        libraryLabel: t('canvas.mention.library', 'Library'),
        inLibraryOnly: t('canvas.mention.inLibraryOnly', 'In Library Only'),
        unavailable: t(
          'canvas.mention.noScope',
          'Open this canvas from a workspace to browse assets',
        ),
      }),
      [t],
    );

    const images = useMemo(() => {
      const needle = search.trim().toLowerCase();
      const matched = needle
        ? inputImages.filter((i) => i.label.toLowerCase().includes(needle))
        : inputImages;
      return matched.slice(0, MENTION_CANDIDATE_LIMIT);
    }, [inputImages, search]);

    // Memoised: a fresh array every render would change `commitActive`'s identity
    // and with it the imperative handle the editor holds — the exact churn
    // `pickRef` exists to prevent.
    const uploadItems = library.uploads.items;
    const generatedItems = library.generated.items;
    const libraryRows = useMemo(() => {
      if (tab === 'uploads') return uploadItems;
      if (tab === 'generated') return canvasId ? generatedItems : NO_ROWS;
      return NO_ROWS;
    }, [tab, canvasId, uploadItems, generatedItems]);

    const count =
      tab === 'input'
        ? images.length
        : tab === 'assets'
          ? assetCount
          : libraryRows.length;

    // Clamped rather than stored blindly: the list shrinks under the user as
    // the search narrows, and an index past the end would make Enter do
    // nothing with no way to tell why.
    const [rawActive, setRawActive] = useState(0);
    const active = count === 0 ? 0 : Math.min(rawActive, count - 1);

    const move = useCallback(
      (delta: number) => {
        // The Assets grid owns its own cursor, so the editor's arrows are
        // forwarded on rather than moving an index it does not read. Every
        // other group is driven from the shared one below.
        if (tab === 'assets') {
          assetsRef.current?.move(delta);
          return;
        }
        setRawActive((i) => {
          if (count === 0) return 0;
          const from = Math.min(i, count - 1);
          return (from + delta + count) % count;
        });
      },
      [tab, count],
    );

    // Read the pick handlers through a ref so `commitActive` — and therefore
    // the imperative handle the editor holds — does not change identity on
    // every parent render.
    const pickRef = useRef({ onPickImage, onPickAsset, onPickLibraryImage });
    pickRef.current = { onPickImage, onPickAsset, onPickLibraryImage };

    /**
     * Keys with a pick still in flight.
     *
     * A real double-click dispatches `click`, `click`, `dblclick` — and all
     * three now reach a commit (the first two through the grid's selection
     * change, the third through `onItemActivate`). Nothing closes the popover
     * in between: the close happens in the CALLER, after `addReferences`
     * resolves, long after the burst is over. For a generated image the extra
     * adds dedupe by url; for an UPLOAD each resolve mints a fresh
     * `generated_media` row with a fresh url, so dedupe cannot fire and one
     * double-click spent three reference slots and left two orphan inbox rows.
     *
     * Keyed rather than a single boolean: a deliberate pick of a DIFFERENT row
     * while the first is in flight is a real second pick, and dropping it
     * would trade one silent defect for another.
     */
    const inFlightRef = useRef(new Set<string>());

    // Both library pick paths — the grid's double click and the editor's Enter —
    // go through here, so the failure echo cannot exist on one and not the
    // other. A caller that answers a result gets a typed reason on screen; a
    // caller that answers nothing keeps the old assume-it-landed behaviour.
    const pickLibrary = useCallback(
      (row: LibraryItem) => {
        const key = libraryKey(row);
        if (inFlightRef.current.has(key)) return;
        inFlightRef.current.add(key);
        setNotice(null);
        void Promise.resolve(pickRef.current.onPickLibraryImage(row))
          .then((r) => {
            if (!r || r.added > 0) return;
            if (r.failed.length > 0) {
              setNotice(
                t('canvas.library.someFailed', {
                  count: r.failed.length,
                  defaultValue: '{{count}} could not be added as references',
                }),
              );
              return;
            }
            if (r.clamped > 0) {
              setNotice(
                t('canvas.library.quotaClamped', {
                  count: r.clamped,
                  defaultValue: '{{count}} references not added · quota reached',
                }),
              );
              return;
            }
            if (r.skipped > 0) {
              setNotice(
                t('canvas.library.alreadyReferenced', {
                  count: r.skipped,
                  defaultValue: '{{count}} already on this node',
                }),
              );
            }
          })
          // A caller that rejects outright is still a pick that added nothing.
          // Contained and LOGGED, not swallowed — an unhandled rejection here
          // would leave the popover looking exactly like success.
          .catch((err: unknown) => {
            console.error('[PromptMentionPicker] library pick failed:', err);
            setNotice(
              t('canvas.library.someFailed', {
                count: 1,
                defaultValue: '{{count}} could not be added as references',
              }),
            );
          })
          // Released only once the round trip is over — the whole point is
          // that the burst of clicks lands INSIDE that window.
          .finally(() => {
            inFlightRef.current.delete(key);
          });
      },
      [t],
    );

    const commitActive = useCallback((): boolean => {
      if (tab === 'input') {
        const image = images[active];
        if (!image) return false;
        pickRef.current.onPickImage(image, inputImages.indexOf(image));
        return true;
      }
      // The grid answers false when nothing is highlighted, and that false is
      // what lets Enter fall through to the text instead of being swallowed.
      if (tab === 'assets') return assetsRef.current?.commitActive() ?? false;
      const row = libraryRows[active];
      if (!row) return false;
      pickLibrary(row);
      return true;
    }, [tab, active, images, libraryRows, inputImages, pickLibrary]);

    const switchTab = useCallback((next: Tab) => {
      setTab(next);
      setRawActive(0);
      setNotice(null);
    }, []);

    // ONE implementation behind both `⇥` paths — the root's own handler (for a
    // synthetic dispatch, and for a pointer user who has clicked into the
    // grid's search box) and the editor's forward through the handle.
    const cycleTab = useCallback(() => {
      setTab((cur) => MENTION_TABS[(MENTION_TABS.indexOf(cur) + 1) % MENTION_TABS.length]);
      setRawActive(0);
      setNotice(null);
    }, []);

    useImperativeHandle(
      ref,
      () => ({ move, commitActive, cycleTab }),
      [move, commitActive, cycleTab],
    );

    const pillClass = (on: boolean): string =>
      `nodrag rounded-full border px-2 py-0.5 text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        on
          ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
          : 'border-canvas-line text-canvas-text hover:border-canvas-strong/60'
      }`;

    return (
      <div
        data-testid="prompt-mention-picker"
        // Opens DOWNWARD: anchored above, it covered the input-image row and
        // collided with the count/size popovers over the footer. z-60 so it
        // wins against those (they sit at z-50) — it is what the user just
        // summoned.
        className="mh-pop-in nodrag nowheel absolute left-0 top-full z-[60] mt-1 flex w-[26rem] max-w-[92vw] flex-col rounded-xl border border-canvas-line bg-canvas-card shadow-lg"
        onMouseDown={(e) => e.preventDefault()}
        onKeyDown={(e) => {
          if (e.key !== 'Tab') return;
          e.preventDefault();
          cycleTab();
        }}
      >
        {/* The first line, before any group: three groups here write three
            DIFFERENT things (a body chip, a bundled asset, a reference on the
            node), and identically-shaped grids cannot say which. */}
        <p
          data-testid="mention-consequence"
          className="border-b border-canvas-line px-2 py-1 text-[10px] leading-snug text-canvas-muted"
        >
          {t(
            'canvas.library.mentionConsequence',
            'Mentioning an asset sends its reference files at run. Mentioning an image adds it as a reference. Plain text stays text.',
          )}
        </p>

        <div className="flex items-center gap-1 border-b border-canvas-line/70 p-1.5">
          {MENTION_TABS.map((name) => (
            <button
              key={name}
              type="button"
              data-testid={`mention-tab-${name}`}
              aria-pressed={tab === name}
              onClick={() => switchTab(name)}
              className={pillClass(tab === name)}
            >
              {t(TAB_LABEL[name][0], TAB_LABEL[name][1])}
            </button>
          ))}
        </div>

        {tab === 'assets' && (
          <AssetGridPicker
            ref={assetsRef}
            query={query}
            labels={assetLabels}
            fetch={fetchAssets}
            fetchKey={scopeId}
            onPick={(row) => onPickAsset(row as AssetSummary)}
            onCountChange={setAssetCount}
            searchBox
            libraryToggle
            unavailable={!scopeId}
            theme="canvas"
            limit={LIMIT}
            debounceMs={DEBOUNCE_MS}
          />
        )}

        {(tab === 'uploads' || tab === 'generated') && (
          <LibraryGrid
            testId="mention-library-grid"
            className="max-h-[15rem] min-h-0 flex-1"
            items={libraryRows}
            loading={library[tab].loading}
            error={library[tab].error}
            onRetry={library[tab].reload}
            query={search}
            onQueryChange={setSearch}
            searchPlaceholder={t('canvas.library.searchPlaceholder', 'Search Library…')}
            selection={[]}
            onSelectionChange={(keys) => {
              // The palette is SINGLE-pick, so a click has to commit here.
              // The grid drives its ring from the controlled `activeIndex`
              // below and its selection from `selection={[]}` — so with a
              // no-op handler a single click reached nothing at all, while
              // the Input Images and Assets tabs of this same popover commit
              // on one press. One palette, two gestures, no error.
              //
              // The base selection is always empty, so a plain or ⌘ click
              // yields exactly one key and that key IS the row just clicked.
              // A ⇧ range is not a single pick and still commits nothing.
              if (keys.length !== 1) return;
              const row = libraryRows.find((r) => libraryKey(r) === keys[0]);
              if (row) pickLibrary(row);
            }}
            consequence={t(
              'canvas.library.mentionAddsReference',
              'Picking one adds it as a reference on this node',
            )}
            activeIndex={active}
            onItemActivate={pickLibrary}
            emptyLabel={
              tab === 'generated' && !canvasId
                ? t(
                    'canvas.library.noCanvas',
                    'Open this canvas from a workspace to see its generations',
                  )
                : t('canvas.library.empty', 'Nothing Here Yet')
            }
            targetRowHeight={84}
          />
        )}

        {(tab === 'uploads' || tab === 'generated') && notice && (
          <p data-testid="mention-notice" className="px-2 pb-1 text-[10px] text-warn">
            {notice}
          </p>
        )}

        {tab === 'input' && (
          <div className="max-h-[15rem] min-h-0 flex-1 overflow-y-auto p-2">
            {images.length === 0 ? (
              <p
                data-testid="mention-input-empty"
                className="px-1 py-2 text-[10px] text-canvas-muted"
              >
                {t('canvas.mention.noInputs', 'No input images on this node')}
              </p>
            ) : (
              <div className="grid grid-cols-4 gap-1.5">
                {images.map((image, i) => (
                  <button
                    key={image.url}
                    type="button"
                    data-testid="mention-input-option"
                    data-active={i === active ? 'true' : 'false'}
                    title={image.label}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onPickImage(image, inputImages.indexOf(image));
                    }}
                    className="nodrag group flex flex-col items-center gap-0.5"
                  >
                    <span
                      className={`h-12 w-12 overflow-hidden rounded-md border ${
                        i === active
                          ? 'border-canvas-strong ring-1 ring-canvas-strong'
                          : 'border-canvas-line/60'
                      }`}
                    >
                      <img
                        src={mediaSrc(image.url)}
                        alt={image.label}
                        className="h-full w-full object-cover"
                      />
                    </span>
                    <span className="max-w-full truncate text-[9px] text-canvas-muted group-hover:text-canvas-text">
                      {image.label}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div
          data-testid="mention-footer"
          className="flex items-center justify-between gap-2 border-t border-canvas-line/70 px-2 py-1 text-[9px] text-canvas-muted"
        >
          <span data-testid="mention-count">
            {t('canvas.mention.count', { count, defaultValue: `${count} results` })}
          </span>
          <span>{t('canvas.mention.hint', 'Up/Down to move · Enter to insert · Esc to close')}</span>
        </div>
      </div>
    );
  },
);

export default PromptMentionPicker;
