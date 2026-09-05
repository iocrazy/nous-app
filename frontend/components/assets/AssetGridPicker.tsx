/**
 * The asset half of an `@` picker — search, type chips, a thumbnail grid and a
 * preview lightbox — shared by the canvas prompt node and the chat composer.
 *
 * Extracted from `features/canvas-core/smart/nodes/PromptMentionPicker.tsx`
 * when chat grew an Assets tab of its own (P5). One grid rather than two
 * because the QUESTION is the same in both places — "which entity from my
 * asset library?" — and the answer is recognised by LOOKING at a picture, not
 * by reading a filename column. Two copies would have drifted on the parts
 * that are easy to get subtly wrong and invisible when wrong: the debounce,
 * the stale-response guard, the active-index clamp.
 *
 * ─ What this component does NOT own ─────────────────────────────────────────
 *
 *   Transport   Injected as `fetch`. The canvas asks `searchAssets(scopeId, …)`
 *               (one workspace, scoped); chat asks `searchAssetsAccessible(…)`
 *               (every team the user belongs to, no scope). Owning it here
 *               would mean this component deciding an AUTHORIZATION question.
 *   Copy        Injected as `labels`. The two hosts write in different i18n
 *               namespaces, and hoisting `t()` calls out of
 *               `PromptMentionPicker.tsx` would break the guard that reads its
 *               `canvas.mention.*` keys straight out of that source file.
 *   Chrome      The popover box, the tab strip and the footer belong to the
 *               host. This renders the rows between them.
 *
 * ─ Keyboard ─────────────────────────────────────────────────────────────────
 *
 * The host's text field keeps focus the whole time (every pick is bound to
 * `mousedown` with `preventDefault`, or a blur would close the popover before
 * a click resolved). So arrows and Enter arrive at the EDITOR and are forwarded
 * here through the imperative handle. A focusable list would take focus off
 * the sentence the user is in the middle of.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ForwardedRef,
  type ReactElement,
  type Ref,
} from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, Loader2, Search } from 'lucide-react';

import { ASSET_TYPES, type AssetType } from './assetSlots';
import {
  ASSET_TYPE_ICON,
  typeSingularKey,
} from '../resources/assets/assetTypeMeta';
import { PinLightbox } from '../resources/assets/sheet/PinLightbox';
import type { AssetLibraryFilter } from '../../services/assetsService';
import { getResourceCoverUrl } from '../../services/resourceService';

/** IC caps the candidate grid; both hosts inherit the same ceiling. */
export const ASSET_GRID_LIMIT = 36;

/** Default debounce. Chat overrides it downwards — its query is the `@` text,
 *  so every keystroke is a new search rather than an occasional one. */
const DEFAULT_DEBOUNCE_MS = 300;

export interface AssetGridPickerHandle {
  /** Move the active tile, wrapping. Driven by the host's arrow keys. */
  move: (delta: number) => void;
  /** Pick the active tile. Returns false when there is nothing to pick — the
   *  host then lets Enter reach the text rather than swallowing it. */
  commitActive: () => boolean;
}

/**
 * The row shape this grid paints.
 *
 * Structural on purpose: both `AssetSummary` (the dialog's narrow view) and
 * `AssetRow` (the full wire row) satisfy it, so neither host has to reshape
 * what its transport returned before handing it over.
 */
export interface AssetGridRow {
  id: string;
  name: string;
  asset_type: AssetType;
  cover_file_id: string | null;
  readiness?: { state: 'ready' | 'draft'; missing: string[] };
  /** Owning team, or null for a system preset. Optional because the narrow
   *  dialog view can omit it; the chat picker snapshots it onto the staged
   *  chip. */
  scope_id?: string | null;
}

/** What the grid asks its transport for. */
export interface AssetGridQuery {
  /** Trimmed, or undefined when empty — never the empty string, which the
   *  backend would treat as a real `q` and match nothing. */
  q: string | undefined;
  type: AssetType | null;
  library: AssetLibraryFilter;
  /** Rows to ask for. Passed through rather than closed over at the call site
   *  so the two hosts cannot end up asking for a different number than the
   *  grid was configured with. */
  limit: number;
}

/** Every string the grid paints. The host owns its own namespace. */
export interface AssetGridLabels {
  /** Only read when `searchBox` is on. */
  searchLabel?: string;
  searchPlaceholder?: string;
  allTypes: string;
  loading: string;
  empty: string;
  error: string;
  preview: string;
  /** Lightbox header. */
  previewGroup: string;
  /** Only read when `libraryToggle` is on. Optional because the toggle is
   *  optional; a host that turns the toggle ON and omits these gets the shared
   *  `canvas.mention.*` wording rather than a blank span and an unlabelled
   *  pill — see the render below. */
  libraryLabel?: string;
  inLibraryOnly?: string;
  /** Only read when `unavailable` is true. */
  unavailable?: string;
}

type Theme = 'canvas' | 'chat';

/**
 * Palette per host. The canvas draws on its own `canvas-*` token family; the
 * chat popover is `ink-*`. Structure and testids are identical either way —
 * only colours differ, which is why this is a lookup rather than a prop for
 * every class.
 *
 * `nodrag` / `nowheel` are load-bearing on the canvas, not decoration: React
 * Flow listens for mousedown on the node and stops propagation, so without
 * them every tile silently does nothing for a real user while a synthetic
 * dispatch in a test still "works".
 */
const THEMES: Record<Theme, Record<string, string>> = {
  canvas: {
    interactive: 'nodrag',
    bar: 'border-b border-canvas-line/70 px-2 py-1.5',
    barLabel: 'text-[10px] font-semibold uppercase tracking-wider text-canvas-muted',
    input: 'bg-transparent text-[11px] text-canvas-text outline-none placeholder:text-canvas-muted',
    icon: 'text-canvas-muted',
    pillOn: 'border-canvas-strong bg-canvas-strong text-canvas-card',
    pillOff: 'border-canvas-line text-canvas-text hover:border-canvas-strong/60',
    body: 'max-h-[15rem] min-h-0 flex-1 overflow-y-auto p-2',
    note: 'px-1 py-2 text-[10px] text-canvas-muted',
    error: 'px-1 py-2 text-[10px] text-warn',
    tileOn: 'border-canvas-strong ring-1 ring-canvas-strong',
    tileOff: 'border-canvas-line/60',
    tileIcon: 'text-canvas-muted',
    caption: 'text-[9px] text-canvas-muted group-hover:text-canvas-text',
    previewKey: 'bg-canvas-strong/90 text-canvas-card',
  },
  chat: {
    interactive: '',
    bar: 'border-b border-ink-800 px-2 py-1.5',
    barLabel: 'text-[10px] font-semibold uppercase tracking-wider text-ink-500',
    input: 'bg-transparent text-[11px] text-ink-200 outline-none placeholder:text-ink-500',
    icon: 'text-ink-500',
    pillOn: 'border-[var(--accent-soft)] bg-[var(--accent-soft)] text-[var(--accent-text)]',
    pillOff: 'border-ink-700 text-ink-400 hover:text-ink-200 hover:border-ink-600',
    body: 'max-h-[288px] min-h-0 flex-1 overflow-y-auto p-2',
    note: 'px-1 py-2 text-[11px] text-ink-500',
    error: 'px-1 py-2 text-[11px] text-warn',
    tileOn: 'border-[var(--accent-text)] ring-1 ring-[var(--accent-text)]',
    tileOff: 'border-ink-700',
    tileIcon: 'text-ink-500',
    caption: 'text-[9px] text-ink-500 group-hover:text-ink-200',
    previewKey: 'bg-ink-900/90 text-ink-200',
  },
};

export interface AssetGridPickerProps<R extends AssetGridRow = AssetGridRow> {
  /** The host's live `@query`. Seeds the search box when there is one, and IS
   *  the query when there is not. */
  query: string;
  /**
   * Controlled search text. When given, the HOST owns the box.
   *
   * The canvas palette shares ONE search term across its four groups — the
   * same string filters the input images and drives the Uploads/Generated
   * grid's box — so the term has to live above this component or the popover
   * would carry two of them. Left undefined (chat's case) the box owns its own
   * text and seeds it from `query`.
   */
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  labels: AssetGridLabels;
  /** Transport. Read through a ref, so an inline arrow at the call site does
   *  not restart the search on every parent render; declare what invalidates
   *  it with `fetchKey`. */
  fetch: (params: AssetGridQuery, signal: AbortSignal) => Promise<R[]>;
  /** Anything outside this component that changes what `fetch` would answer
   *  (the canvas's scope id). Changing it refetches. */
  fetchKey?: string;
  onPick: (row: R) => void;
  /**
   * How many rows are showing, after the display cap.
   *
   * Fired when an ANSWER arrives, never from a render effect. A host told "0"
   * before anything was asked would paint "Assets 0", which reads as "your
   * library is empty" rather than "nobody has asked yet".
   */
  onCountChange?: (count: number) => void;
  /**
   * Is this grid the host's visible tab?
   *
   * `false` renders nothing and asks nothing (an in-flight request is aborted
   * on the way out), but the component STAYS MOUNTED — so a search the user
   * refined by hand, and the type chip they picked, survive a trip to the
   * host's other tab and back. Unmounting instead resets both, which is the
   * one thing switching tabs must not silently take away.
   */
  active?: boolean;
  /** Render the search box. Off for chat, where the `@` text IS the query and
   *  a second box would be two places to type one thing. */
  searchBox?: boolean;
  /** Render the "In Library Only" pill. Both hosts show it: the grid opens
   *  narrowed, so a host without the pill would be a shelf the user cannot
   *  widen. */
  libraryToggle?: boolean;
  /**
   * Where the pill starts. `true` — library members only — and the default
   * is the decision.
   *
   * A library member is one somebody ADDED (mig 449, user ruling reaffirmed
   * 2026-09-05). Script imports and the rows the P4 legacy-card migration
   * created were never added, so listing them would present as library
   * members things that are not. `false` is for a host that means "everything
   * this scope can see" and says so.
   */
  defaultInLibraryOnly?: boolean;
  /**
   * The grid cannot answer at all — the canvas's "opened without a workspace"
   * case. A distinct state from an error and from an empty result: nothing was
   * asked, so nothing failed and nothing was missing.
   */
  unavailable?: boolean;
  theme?: Theme;
  /** Rows per request. The DISPLAY cap is {@link ASSET_GRID_LIMIT}. */
  limit?: number;
  debounceMs?: number;
}

/**
 * Generic over the ROW, so a host handing in a narrower transport gets its own
 * type back out of `onPick`.
 *
 * `AssetGridRow` leaves `readiness` / `scope_id` optional because the narrow
 * dialog view can omit them; `AssetSummary` does not. Pinning the component to
 * the loose row would force every canvas pick through an unchecked
 * `as AssetSummary` — a cast sitting exactly where the two row shapes are the
 * only thing that could ever diverge.
 */
function AssetGridPickerInner<R extends AssetGridRow>(
  {
    query,
    searchValue,
    onSearchChange,
    labels,
    fetch,
    fetchKey = '',
    onPick,
    onCountChange,
    active = true,
    searchBox = true,
    libraryToggle = false,
    defaultInLibraryOnly = true,
    unavailable = false,
    theme = 'canvas',
    limit = 60,
    debounceMs = DEFAULT_DEBOUNCE_MS,
  }: AssetGridPickerProps<R>,
  ref: ForwardedRef<AssetGridPickerHandle>,
): ReactElement | null {
  const { t } = useTranslation();
  const c = THEMES[theme];

  // The type names and the readiness word are the SAME question in both
  // hosts and already live in a shared namespace, so they stay here rather
  // than being routed through `labels` — which carries only the strings the
  // two hosts genuinely word differently.
  const typeLabel = (type: AssetType): string => t(typeSingularKey(type), type);

  // The search box is the single source of the query. The host's `@query`
  // seeds it during RENDER rather than in an effect: an effect that mirrors
  // a prop into state re-runs after the user has already typed here and
  // swallows the edit (the useEffect-prop-seed trap). Comparing against the
  // previous PROP value means typing in the box never triggers a resync.
  const controlled = searchValue !== undefined;
  const [ownSearch, setOwnSearch] = useState(query);
  const lastQueryRef = useRef(query);
  // Only when UNCONTROLLED: a controlled host already seeds its own state from
  // the same `@query`, and reseeding here as well would be two writers for one
  // value.
  if (!controlled && query !== lastQueryRef.current) {
    lastQueryRef.current = query;
    setOwnSearch(query);
  }
  const search = controlled ? searchValue : ownSearch;
  const setSearch = useCallback(
    (value: string) => {
      if (onSearchChange) onSearchChange(value);
      else setOwnSearch(value);
    },
    [onSearchChange],
  );

  const [debounced, setDebounced] = useState(search);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), debounceMs);
    return () => clearTimeout(timer);
  }, [search, debounceMs]);

  const [type, setType] = useState<AssetType | null>(null);
  /**
   * Seeded from the host ONCE, on purpose.
   *
   * `useState` reads its argument on the first render only, so a host that
   * flips `defaultInLibraryOnly` later does not yank the shelf out from under
   * a user who has pressed the pill themselves. The default value, and why it
   * is `true`, is documented on the prop.
   */
  const [inLibraryOnly, setInLibraryOnly] = useState(defaultInLibraryOnly);

  const [rows, setRows] = useState<R[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState(false);
  const [preview, setPreview] = useState<{ ids: string[]; index: number } | null>(
    null,
  );

  const fetchRef = useRef(fetch);
  fetchRef.current = fetch;

  const onCountChangeRef = useRef(onCountChange);
  onCountChangeRef.current = onCountChange;

  useEffect(() => {
    // A hidden tab that kept polling would spend four round trips per
    // keystroke on a list nobody is reading. The STATE stays; only the asking
    // stops, and switching back re-asks.
    if (!active || unavailable) return undefined;
    // The debounce has not caught up with what the box says yet, so the only
    // request we could make now is for a query the user has already moved past.
    // Skipping is not the same as waiting: the dependency change ran the
    // previous cleanup first, so an in-flight request for the older query is
    // already aborted, and nothing is in flight while the user is typing.
    if (debounced !== search) return undefined;
    // AbortController rather than a `cancelled` flag: the flag only stops a
    // stale response from being APPLIED, while this also stops it being
    // waited for. The search endpoint costs four round trips per call, so a
    // four-character query would otherwise leave three of them in flight.
    const controller = new AbortController();
    setLoading(true);
    setListError(false);
    fetchRef
      .current(
        {
          q: debounced.trim() || undefined,
          type,
          library: inLibraryOnly ? 'in' : 'all',
          limit,
        },
        controller.signal,
      )
      .then((found) => {
        if (controller.signal.aborted) return;
        setRows(found);
        setLoading(false);
        onCountChangeRef.current?.(found.slice(0, ASSET_GRID_LIMIT).length);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // An abort raised by a transport that honours the signal is not a
        // failure — reporting it would paint "could not load the library"
        // over a search the user themselves superseded.
        if (err instanceof Error && err.name === 'AbortError') return;
        console.error('[AssetGridPicker] asset search failed:', err);
        setListError(true);
        setLoading(false);
      });
    return () => controller.abort();
  }, [active, unavailable, debounced, search, type, inLibraryOnly, fetchKey, limit]);

  const items = useMemo(() => rows.slice(0, ASSET_GRID_LIMIT), [rows]);
  const count = items.length;

  // Clamped rather than stored blindly: the list shrinks under the user as
  // the search narrows, and an index past the end would make Enter do
  // nothing with no way to tell why.
  const [rawActive, setRawActive] = useState(0);
  const activeIndex = count === 0 ? 0 : Math.min(rawActive, count - 1);

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

  // Read the pick handler through a ref so `commitActive` — and therefore
  // the handle the host holds — does not change identity on every render.
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  const commitActive = useCallback((): boolean => {
    const row = items[activeIndex];
    if (!row) return false;
    pickRef.current(row);
    return true;
  }, [items, activeIndex]);

  useImperativeHandle(ref, () => ({ move, commitActive }), [move, commitActive]);

  const typeChips = useMemo(() => [null, ...ASSET_TYPES] as const, []);

  // Every hook above has run — the state they hold is exactly what survives a
  // trip to the host's other tab. Only the OUTPUT is withheld.
  if (!active) return null;

  const pill = (on: boolean): string =>
    `${c.interactive} rounded-full border px-2 py-0.5 text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
      on ? c.pillOn : c.pillOff
    }`;

  return (
    <>
      {(searchBox || libraryToggle) && (
        <div
          className={`flex items-center gap-1.5 ${c.bar}`}
          // Keep focus on the host's editor: a bare <button> takes it on
          // mousedown, and the `@query` tracker lives on that editor — so a
          // chip click would silently end the mention session. The canvas
          // popover prevents this at its root; the chat one does not, and
          // the search input below re-claims focus explicitly.
          onMouseDown={(e) => e.preventDefault()}
        >
          {libraryToggle && (
            <>
              {/* One library per canvas — the route's team segment IS the
                  scope, so there is nothing to choose between and a select
                  with a single option would be a control that does nothing. */}
              {/* Fallbacks, not `?? ''`. These two are optional in the type
                  because the toggle is optional, so a host that turns the
                  toggle on and forgets them would render a blank span and a
                  pill with no words in it — a control the user cannot read is
                  worse than one the host worded itself. The shared
                  `canvas.mention.*` strings are what both hosts already say. */}
              <span className={`shrink-0 ${c.barLabel}`}>
                {labels.libraryLabel ?? t('canvas.mention.library', 'Library')}
              </span>
              <button
                type="button"
                data-testid="mention-library-toggle"
                aria-pressed={inLibraryOnly}
                onClick={() => setInLibraryOnly((v) => !v)}
                className={pill(inLibraryOnly)}
              >
                {labels.inLibraryOnly ?? t('canvas.mention.inLibraryOnly', 'In Library Only')}
              </button>
            </>
          )}
          {searchBox && (
            <span className="ml-auto flex min-w-0 flex-1 items-center gap-1">
              <Search size={12} className={`shrink-0 ${c.icon}`} />
              <input
                data-testid="mention-search"
                aria-label={labels.searchLabel}
                placeholder={labels.searchPlaceholder}
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setRawActive(0);
                }}
                // The host's editor owns focus (see the header note), so this
                // box is for the mouse path. mousedown is prevented on the
                // popover, so give it focus explicitly when it is clicked.
                onMouseDown={(e) => {
                  e.stopPropagation();
                  e.currentTarget.focus();
                }}
                className={`${c.interactive} min-w-0 flex-1 ${c.input}`}
              />
            </span>
          )}
        </div>
      )}

      <div
        className={`flex flex-wrap gap-1 ${c.bar}`}
        onMouseDown={(e) => e.preventDefault()}
      >
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
            className={pill(type === chip)}
          >
            {chip === null ? labels.allTypes : typeLabel(chip)}
          </button>
        ))}
      </div>

      <div className={c.body} data-testid="mention-assets-body">
        {unavailable ? (
          <p data-testid="mention-assets-error" className={c.error}>
            {labels.unavailable}
          </p>
        ) : listError ? (
          <p data-testid="mention-assets-error" className={c.error}>
            {labels.error}
          </p>
        ) : loading ? (
          <p
            data-testid="mention-assets-loading"
            className={`flex items-center gap-1.5 ${c.note}`}
          >
            <Loader2 size={11} className="animate-spin" />
            {labels.loading}
          </p>
        ) : items.length === 0 ? (
          <p data-testid="mention-assets-empty" className={c.note}>
            {labels.empty}
          </p>
        ) : (
          <div className="grid grid-cols-4 gap-1.5">
            {items.map((row, i) => {
              const Icon = ASSET_TYPE_ICON[row.asset_type] ?? ASSET_TYPE_ICON.prop;
              return (
                // `group` belongs HERE, on the wrapper — Tailwind compiles
                // `group-hover:` to `.group:hover .group-hover\:…`, so with
                // the class on the sibling pick button instead, the preview
                // key had no `.group` ancestor and its reveal rule never
                // matched.
                <div
                  key={row.id}
                  className="group relative flex flex-col items-center gap-0.5"
                >
                  <button
                    type="button"
                    data-testid="mention-asset-option"
                    data-asset-id={row.id}
                    data-active={i === activeIndex ? 'true' : 'false'}
                    title={`${row.name} · ${typeLabel(row.asset_type)}${
                      row.readiness?.state === 'draft'
                        ? ` · ${t('assets.readiness.draft', 'Draft')}`
                        : ''
                    }`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onPick(row);
                    }}
                    className={`${c.interactive} flex w-full flex-col items-center gap-0.5`}
                  >
                    <span
                      className={`flex h-12 w-12 items-center justify-center overflow-hidden rounded-md border ${c.tileIcon} ${
                        i === activeIndex ? c.tileOn : c.tileOff
                      }`}
                    >
                      {row.cover_file_id ? (
                        <img
                          // `getResourceCoverUrl` already returns an ABSOLUTE
                          // url against the API origin, so there is nothing
                          // for `mediaSrc` to absolutize here — a bare
                          // relative src is the thing that 404s when the app
                          // and the API are different hosts, and this is not
                          // one.
                          src={getResourceCoverUrl(row.cover_file_id)}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <Icon size={16} />
                      )}
                    </span>
                    <span className={`max-w-full truncate ${c.caption}`}>
                      {row.name}
                    </span>
                  </button>
                  {row.cover_file_id && (
                    <button
                      type="button"
                      data-testid="mention-asset-preview"
                      data-asset-id={row.id}
                      aria-label={labels.preview}
                      title={labels.preview}
                      onMouseDown={(e) => {
                        // Preview must not also insert: stop the pick
                        // button's handler from seeing this press.
                        e.preventDefault();
                        e.stopPropagation();
                        const withCovers = items.filter((a) => a.cover_file_id);
                        setPreview({
                          ids: withCovers.map((a) => String(a.cover_file_id)),
                          index: Math.max(
                            0,
                            withCovers.findIndex((a) => a.id === row.id),
                          ),
                        });
                      }}
                      // Revealed by hovering the TILE, not by finding the
                      // key. `focus:` keeps it reachable without a pointer.
                      className={`${c.interactive} absolute right-0 top-0 rounded-bl-md rounded-tr-md p-0.5 opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:opacity-100 focus:opacity-100 ${c.previewKey}`}
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

      {preview && (
        <PinLightbox
          resourceIds={preview.ids}
          index={preview.index}
          slotLabel={labels.previewGroup}
          onIndexChange={(next) => setPreview({ ...preview, index: next })}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  );
}

/**
 * `forwardRef` erases type parameters, so the cast is what gives the generic
 * back at the call site. The signature below is the one callers see, and it is
 * checked against the implementation by `forwardRef(AssetGridPickerInner)`
 * having to accept it.
 */
export const AssetGridPicker = forwardRef(AssetGridPickerInner) as <
  R extends AssetGridRow,
>(
  props: AssetGridPickerProps<R> & { ref?: Ref<AssetGridPickerHandle> },
) => ReactElement | null;

export default AssetGridPicker;
