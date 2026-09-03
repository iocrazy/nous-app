/**
 * The `@` picker on a canvas prompt node.
 *
 * Replaces the tall whole-resource-library list that used to open here. That
 * list answered "which file in my library?", which is not the question someone
 * typing `@` in a prompt is asking — they mean "which of the pictures already
 * feeding this node?" or "which entity from my asset library?". So there are
 * two tabs and both are THUMBNAIL GRIDS: a picture is recognised by looking at
 * it, and a filename column made the user read instead of look.
 *
 * ─ What a pick does ─────────────────────────────────────────────────────────
 *
 *   Input Images  inserts an `@Image N` chip and marks that picture as the
 *                 node's i2i source. No new state — the picture was already an
 *                 input.
 *   Assets        inserts an ASSET chip. No node is created: the mention IS
 *                 the reference, and at run time it is bundled exactly like a
 *                 wired asset card (see `mentionedAssets.ts`).
 *
 * ─ Keyboard ────────────────────────────────────────────────────────────────
 *
 * The editor keeps focus the whole time (every pick is bound to `mousedown`
 * with `preventDefault`, or the editor's blur would close this popover before
 * a click resolved). So the arrow keys and Enter arrive at the EDITOR, and it
 * forwards them here through the imperative handle. The alternative — a
 * focusable list — would take focus off the text the user is mid-sentence in.
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
import { mediaSrc } from '../mediaUrl';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

/** IC caps the candidate grid; the same ceiling applies to both tabs. */
export const MENTION_CANDIDATE_LIMIT = 36;

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
}

interface Props {
  /** `teams.id` for the assets calls. '' means the route has no team segment,
   *  which is a 403 rather than an unscoped query — the tab says so. */
  scopeId: string;
  inputImages: MentionInputImage[];
  onPickImage: (image: MentionInputImage, index: number) => void;
  onPickAsset: (asset: AssetSummary) => void;
  /** The live `@query` the editor reports, which seeds the search box. */
  query: string;
}

type Tab = 'input' | 'assets';

export const PromptMentionPicker = forwardRef<PromptMentionPickerHandle, Props>(
  function PromptMentionPicker(
    { scopeId, inputImages, onPickImage, onPickAsset, query },
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

    const count = tab === 'input' ? images.length : assets.length;

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
    const pickRef = useRef({ onPickImage, onPickAsset });
    pickRef.current = { onPickImage, onPickAsset };

    const commitActive = useCallback((): boolean => {
      if (tab === 'input') {
        const image = images[active];
        if (!image) return false;
        pickRef.current.onPickImage(image, inputImages.indexOf(image));
        return true;
      }
      const asset = assets[active];
      if (!asset) return false;
      pickRef.current.onPickAsset(asset);
      return true;
    }, [tab, active, images, assets, inputImages]);

    useImperativeHandle(ref, () => ({ move, commitActive }), [move, commitActive]);

    const switchTab = useCallback((next: Tab) => {
      setTab(next);
      setRawActive(0);
    }, []);

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
      >
        <div className="flex items-center gap-1 border-b border-canvas-line/70 p-1.5">
          <button
            type="button"
            data-testid="mention-tab-input"
            aria-pressed={tab === 'input'}
            onClick={() => switchTab('input')}
            className={pillClass(tab === 'input')}
          >
            {t('canvas.mention.tabInput', 'Input Images')}
          </button>
          <button
            type="button"
            data-testid="mention-tab-assets"
            aria-pressed={tab === 'assets'}
            onClick={() => switchTab('assets')}
            className={pillClass(tab === 'assets')}
          >
            {t('canvas.mention.tabAssets', 'Assets')}
          </button>
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
