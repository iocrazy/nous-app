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
import { Eye, Loader2, Search } from 'lucide-react';

import {
  ASSET_TYPES,
  type AssetType,
} from '../../../../components/assets/assetSlots';
import {
  ASSET_TYPE_ICON,
  typeSingularKey,
} from '../../../../components/resources/assets/assetTypeMeta';
import { PinLightbox } from '../../../../components/resources/assets/sheet/PinLightbox';
import {
  searchAssets,
  type AssetSummary,
} from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import type { AddReferencesResult } from '../../library/addReferences';
import { LibraryGrid } from '../../library/LibraryGrid';
import { useLibrarySearch, type LibraryItem } from '../../library/librarySearch';
import { mediaSrc } from '../mediaUrl';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

/** IC caps the candidate grid; the same ceiling applies to both tabs. */
export const MENTION_CANDIDATE_LIMIT = 36;

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

    const [type, setType] = useState<AssetType | null>(null);
    /**
     * `all` by default, and the default is the decision.
     *
     * The server's default is `in` — library members only — which hides script
     * imports and every asset the P4 legacy-card migration created. Those are
     * exactly the assets a canvas points at, so a user whose card resolves to a
     * migrated asset would see it on the board and be unable to `@` it. The
     * toggle lets someone narrow to the shelf on purpose; it never narrows on
     * their behalf. (Same reasoning as `AssetPickerDialog`.)
     */
    const [inLibraryOnly, setInLibraryOnly] = useState(false);

    const [rows, setRows] = useState<AssetSummary[]>([]);
    const [loading, setLoading] = useState(false);
    const [listError, setListError] = useState(false);
    const [preview, setPreview] = useState<{ ids: string[]; index: number } | null>(
      null,
    );
    /** Why the last library pick changed nothing. null while nothing is wrong. */
    const [notice, setNotice] = useState<string | null>(null);

    useEffect(() => {
      if (tab !== 'assets') return undefined;
      if (!scopeId) {
        setListError(true);
        return undefined;
      }
      let cancelled = false;
      setLoading(true);
      setListError(false);
      searchAssets(scopeId, {
        q: debounced.trim() || undefined,
        type: type ?? undefined,
        limit: LIMIT,
        library: inLibraryOnly ? 'in' : 'all',
      })
        .then((found) => {
          if (cancelled) return;
          setRows(found);
          setLoading(false);
        })
        .catch((err) => {
          if (cancelled) return;
          console.error('[PromptMentionPicker] searchAssets failed:', err);
          setListError(true);
          setLoading(false);
        });
      return () => {
        cancelled = true;
      };
    }, [tab, scopeId, debounced, type, inLibraryOnly]);

    const images = useMemo(() => {
      const needle = search.trim().toLowerCase();
      const matched = needle
        ? inputImages.filter((i) => i.label.toLowerCase().includes(needle))
        : inputImages;
      return matched.slice(0, MENTION_CANDIDATE_LIMIT);
    }, [inputImages, search]);

    const assets = useMemo(() => rows.slice(0, MENTION_CANDIDATE_LIMIT), [rows]);

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
      tab === 'input' ? images.length : tab === 'assets' ? assets.length : libraryRows.length;

    // Clamped rather than stored blindly: the list shrinks under the user as
    // the search narrows, and an index past the end would make Enter do
    // nothing with no way to tell why.
    const [rawActive, setRawActive] = useState(0);
    const active = count === 0 ? 0 : Math.min(rawActive, count - 1);

    const move = useCallback(
      (delta: number) => {
        setRawActive((i) => {
          if (count === 0) return 0;
          const from = Math.min(i, count - 1);
          return (from + delta + count) % count;
        });
      },
      [count],
    );

    // Read the pick handlers through a ref so `commitActive` — and therefore
    // the imperative handle the editor holds — does not change identity on
    // every parent render.
    const pickRef = useRef({ onPickImage, onPickAsset, onPickLibraryImage });
    pickRef.current = { onPickImage, onPickAsset, onPickLibraryImage };

    // Both library pick paths — the grid's double click and the editor's Enter —
    // go through here, so the failure echo cannot exist on one and not the
    // other. A caller that answers a result gets a typed reason on screen; a
    // caller that answers nothing keeps the old assume-it-landed behaviour.
    const pickLibrary = useCallback(
      (row: LibraryItem) => {
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
      if (tab === 'assets') {
        const asset = assets[active];
        if (!asset) return false;
        pickRef.current.onPickAsset(asset);
        return true;
      }
      const row = libraryRows[active];
      if (!row) return false;
      pickLibrary(row);
      return true;
    }, [tab, active, images, assets, libraryRows, inputImages, pickLibrary]);

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

    const typeChips = useMemo(() => [null, ...ASSET_TYPES] as const, []);

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
          <>
            <div className="flex items-center gap-1.5 border-b border-canvas-line/70 px-2 py-1.5">
              {/* One library per canvas — the route's team segment IS the
                  scope, so there is nothing to choose between and a select
                  with a single option would be a control that does nothing. */}
              <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-canvas-muted">
                {t('canvas.mention.library', 'Library')}
              </span>
              <button
                type="button"
                data-testid="mention-library-toggle"
                aria-pressed={inLibraryOnly}
                onClick={() => setInLibraryOnly((v) => !v)}
                className={pillClass(inLibraryOnly)}
              >
                {t('canvas.mention.inLibraryOnly', 'In Library Only')}
              </button>
              <span className="ml-auto flex min-w-0 flex-1 items-center gap-1">
                <Search size={12} className="shrink-0 text-canvas-muted" />
                <input
                  data-testid="mention-search"
                  aria-label={t('canvas.mention.searchLabel', 'Search assets')}
                  placeholder={t('canvas.mention.searchPlaceholder', 'Search assets…')}
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setRawActive(0);
                  }}
                  // The editor owns focus (see the header note), so this box is
                  // for the mouse path. mousedown is prevented on the popover,
                  // so give it focus explicitly when it is clicked.
                  onMouseDown={(e) => {
                    e.stopPropagation();
                    e.currentTarget.focus();
                  }}
                  className="nodrag min-w-0 flex-1 bg-transparent text-[11px] text-canvas-text outline-none placeholder:text-canvas-muted"
                />
              </span>
            </div>
            <div className="flex flex-wrap gap-1 border-b border-canvas-line/70 px-2 py-1.5">
              {typeChips.map((chip) => (
                <button
                  key={chip ?? '__all'}
                  type="button"
                  data-testid="mention-type-chip"
                  data-type={chip ?? 'all'}
                  aria-pressed={type === chip}
                  onClick={() => {
                    setType(chip);
                    setRawActive(0);
                  }}
                  className={pillClass(type === chip)}
                >
                  {chip === null
                    ? t('canvas.mention.allTypes', 'All')
                    : t(typeSingularKey(chip), chip)}
                </button>
              ))}
            </div>
          </>
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
            onSelectionChange={() => {}}
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

        {(tab === 'input' || tab === 'assets') && (
          <div className="max-h-[15rem] min-h-0 flex-1 overflow-y-auto p-2">
            {tab === 'input' ? (
              images.length === 0 ? (
                <p data-testid="mention-input-empty" className="px-1 py-2 text-[10px] text-canvas-muted">
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
                          i === active ? 'border-canvas-strong ring-1 ring-canvas-strong' : 'border-canvas-line/60'
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
              )
            ) : !scopeId ? (
              <p data-testid="mention-assets-error" className="px-1 py-2 text-[10px] text-warn">
                {t(
                  'canvas.mention.noScope',
                  'Open this canvas from a workspace to browse assets',
                )}
              </p>
            ) : listError ? (
              <p data-testid="mention-assets-error" className="px-1 py-2 text-[10px] text-warn">
                {t('canvas.mention.loadFailed', 'Could not load the asset library')}
              </p>
            ) : loading ? (
              <p className="flex items-center gap-1.5 px-1 py-2 text-[10px] text-canvas-muted">
                <Loader2 size={11} className="animate-spin" />
                {t('canvas.mention.loading', 'Loading…')}
              </p>
            ) : assets.length === 0 ? (
              <p data-testid="mention-assets-empty" className="px-1 py-2 text-[10px] text-canvas-muted">
                {t('canvas.mention.noResults', 'No assets found')}
              </p>
            ) : (
              <div className="grid grid-cols-4 gap-1.5">
                {assets.map((asset, i) => {
                  const Icon = ASSET_TYPE_ICON[asset.asset_type] ?? ASSET_TYPE_ICON.prop;
                  return (
                    // `group` belongs HERE, on the wrapper — Tailwind compiles
                    // `group-hover:` to `.group:hover .group-hover\:…`, so with
                    // the class on the sibling pick button instead, the preview
                    // key had no `.group` ancestor and its reveal rule never
                    // matched. The affordance existed only for a pointer that
                    // had already found an invisible 14px target in the corner,
                    // and the tests locate it by testid so nothing caught it.
                    <div
                      key={asset.id}
                      className="group relative flex flex-col items-center gap-0.5"
                    >
                      <button
                        type="button"
                        data-testid="mention-asset-option"
                        data-asset-id={asset.id}
                        data-active={i === active ? 'true' : 'false'}
                        title={`${asset.name} · ${t(typeSingularKey(asset.asset_type), asset.asset_type)}${
                          asset.readiness?.state === 'draft'
                            ? ` · ${t('assets.readiness.draft', 'Draft')}`
                            : ''
                        }`}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          onPickAsset(asset);
                        }}
                        className="nodrag flex w-full flex-col items-center gap-0.5"
                      >
                        <span
                          className={`flex h-12 w-12 items-center justify-center overflow-hidden rounded-md border text-canvas-muted ${
                            i === active
                              ? 'border-canvas-strong ring-1 ring-canvas-strong'
                              : 'border-canvas-line/60'
                          }`}
                        >
                          {asset.cover_file_id ? (
                            <img
                              src={mediaSrc(getResourceCoverUrl(asset.cover_file_id))}
                              alt=""
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <Icon size={16} />
                          )}
                        </span>
                        <span className="max-w-full truncate text-[9px] text-canvas-muted group-hover:text-canvas-text">
                          {asset.name}
                        </span>
                      </button>
                      {asset.cover_file_id && (
                        <button
                          type="button"
                          data-testid="mention-asset-preview"
                          data-asset-id={asset.id}
                          aria-label={t('canvas.mention.preview', 'Preview')}
                          title={t('canvas.mention.preview', 'Preview')}
                          onMouseDown={(e) => {
                            // Preview must not also insert: stop the pick
                            // button's handler from seeing this press.
                            e.preventDefault();
                            e.stopPropagation();
                            const withCovers = assets.filter((a) => a.cover_file_id);
                            setPreview({
                              ids: withCovers.map((a) => String(a.cover_file_id)),
                              index: Math.max(
                                0,
                                withCovers.findIndex((a) => a.id === asset.id),
                              ),
                            });
                          }}
                          // Revealed by hovering the TILE, not by finding the
                          // key. `focus:` keeps it reachable without a pointer.
                          className="nodrag absolute right-0 top-0 rounded-bl-md rounded-tr-md bg-canvas-strong/90 p-0.5 text-canvas-card opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:opacity-100 focus:opacity-100"
                        >
                          <Eye size={10} />
                        </button>
                      )}
                    </div>
                  );
                })}
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

        {preview && (
          <PinLightbox
            resourceIds={preview.ids}
            index={preview.index}
            slotLabel={t('canvas.mention.tabAssets', 'Assets')}
            onIndexChange={(next) => setPreview({ ...preview, index: next })}
            onClose={() => setPreview(null)}
          />
        )}
      </div>
    );
  },
);

export default PromptMentionPicker;
