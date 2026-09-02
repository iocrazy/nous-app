/**
 * "Pick an asset from the library" modal — the one entry point behind BOTH
 * canvas affordances (the top node bar's Asset chip and the drag-create
 * menu's Asset card), so the two can never offer different libraries.
 *
 * Why it fetches the DETAIL after a pick, rather than handing the search row
 * straight to the factory: `selected_file_ids` is seeded from the asset's
 * primary-slot files, and the list endpoint answers `file_counts_by_slot`
 * (a tally) but not the file rows. Without this hop a freshly placed card
 * would reference nothing and the user would have to tick every box by hand.
 *
 * A failed detail fetch does NOT fall back to placing an empty card: that
 * would look like success and quietly drop the seeding. It reports and lets
 * the user try again (CLAUDE.md — silent no-op 不可接受).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Loader2, Search, X } from 'lucide-react';

import {
  ASSET_TYPES,
  type AssetType,
} from '../../../../components/assets/assetSlots';
import {
  ASSET_TYPE_ICON,
  typeSingularKey,
} from '../../../../components/resources/assets/assetTypeMeta';
import {
  fetchAssetDetail,
  searchAssets,
  type AssetSummary,
} from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import type { AssetNodeSeed } from '../factories';
import { useCanvasScope } from '../canvasScope';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

export interface AssetPickerDialogProps {
  /** Called with the asset's DETAIL row — enough to seed the node's files. */
  onPick: (asset: AssetNodeSeed) => void;
  onClose: () => void;
}

export function AssetPickerDialog({ onPick, onClose }: AssetPickerDialogProps) {
  const { t } = useTranslation();
  const { scopeId } = useCanvasScope();

  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [type, setType] = useState<AssetType | null>(null);
  const [rows, setRows] = useState<AssetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState(false);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [pickError, setPickError] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!scopeId) {
      setListError(true);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setListError(false);
    // `library: 'all'` — EXPLICIT, and the explicitness is the point.
    //
    // This call named itself the decision point for the day a membership
    // filter arrived. It has (mig 449), and its SERVER DEFAULT is `'in'`, so
    // omitting the parameter here would silently narrow this picker to library
    // members — hiding script imports and, more to the point, every asset the
    // P4 legacy-card migration created. That is exactly the population this
    // canvas points at: a user whose old card resolved to a migrated asset
    // would see it on the board and be unable to find it in the picker.
    //
    // The other callers of `searchAssets` (attach a generation, link into a
    // project) genuinely do want the shelf, which is why the default stays
    // `'in'` and this one caller opts out rather than the default changing.
    searchAssets(scopeId, {
      q: debounced.trim() || undefined,
      type: type ?? undefined,
      limit: LIMIT,
      library: 'all',
    })
      .then((found) => {
        if (cancelled) return;
        setRows(found);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AssetPickerDialog] searchAssets failed:', err);
        setListError(true);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, debounced, type]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const pick = useCallback(
    (row: AssetSummary) => {
      if (!scopeId || pickingId !== null) return;
      setPickingId(row.id);
      setPickError(false);
      fetchAssetDetail(scopeId, row.id)
        .then((detail) => {
          onPick(detail);
          onClose();
        })
        .catch((err) => {
          console.error('[AssetPickerDialog] fetchAssetDetail failed:', err);
          setPickingId(null);
          setPickError(true);
        });
    },
    [scopeId, pickingId, onPick, onClose],
  );

  const typeChips = useMemo(() => [null, ...ASSET_TYPES] as const, []);

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-8"
      onClick={onClose}
      data-testid="asset-picker-backdrop"
    >
      <div
        role="dialog"
        aria-label={t('canvas.asset.pickerTitle', 'Add Asset')}
        className="mh-pop-in flex max-h-[70vh] w-[34rem] max-w-[92vw] flex-col rounded-xl border border-ink-700 bg-ink-900/95 shadow-xl backdrop-blur"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-ink-700 p-2.5">
          <Search size={14} className="shrink-0 text-ink-500" />
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-xs text-ink-100 outline-none placeholder:text-ink-500"
            aria-label={t('canvas.asset.searchLabel', 'Search assets')}
            placeholder={t('canvas.asset.searchPlaceholder', 'Search assets…')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button
            type="button"
            aria-label={t('common.close', 'Close')}
            onClick={onClose}
            className="shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-100"
          >
            <X size={14} />
          </button>
        </div>

        <div className="flex flex-wrap gap-1 border-b border-ink-700 p-2">
          {typeChips.map((chip) => (
            <button
              key={chip ?? '__all'}
              type="button"
              onClick={() => setType(chip)}
              aria-pressed={type === chip}
              className={`rounded-full border px-2 py-0.5 text-[11px] ${
                type === chip
                  ? 'border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'border-ink-700 text-ink-500 hover:text-ink-300'
              }`}
            >
              {chip === null
                ? t('canvas.asset.allTypes', 'All')
                : t(typeSingularKey(chip), chip)}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {pickError && (
            <div
              data-testid="asset-picker-pick-error"
              className="mb-2 rounded border border-warn-line bg-warn-soft px-2 py-1 text-[11px] text-warn"
            >
              {t('canvas.asset.pickFailed', 'Could not open that asset. Try again.')}
            </div>
          )}
          {listError ? (
            <div data-testid="asset-picker-error" className="p-3 text-xs text-warn">
              {t('canvas.asset.listFailed', 'Could not load the asset library')}
            </div>
          ) : loading ? (
            <div className="flex items-center gap-2 p-3 text-xs text-ink-500">
              <Loader2 size={13} className="animate-spin" />
              {t('canvas.asset.loading', 'Loading…')}
            </div>
          ) : rows.length === 0 ? (
            <div data-testid="asset-picker-empty" className="p-3 text-xs text-ink-500">
              {t('canvas.asset.noResults', 'No assets found')}
            </div>
          ) : (
            <ul className="grid grid-cols-2 gap-1">
              {rows.map((row) => {
                const Icon = ASSET_TYPE_ICON[row.asset_type] ?? ASSET_TYPE_ICON.prop;
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      data-testid="asset-picker-row"
                      data-asset-id={row.id}
                      disabled={pickingId !== null}
                      onClick={() => pick(row)}
                      className="flex w-full items-center gap-2 rounded-lg p-1.5 text-left transition-colors hover:bg-ink-800 disabled:opacity-60"
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded border border-ink-700 bg-ink-950/40 text-ink-300">
                        {row.cover_file_id ? (
                          <img
                            src={getResourceCoverUrl(row.cover_file_id)}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <Icon size={14} />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs text-ink-100">
                          {row.name}
                        </span>
                        <span className="block truncate text-[10px] text-ink-500">
                          {t(typeSingularKey(row.asset_type), row.asset_type)}
                          {row.readiness?.state === 'draft'
                            ? ` · ${t('assets.readiness.draft', 'Draft')}`
                            : ''}
                        </span>
                      </span>
                      {pickingId === row.id && (
                        <Loader2 size={12} className="shrink-0 animate-spin text-ink-500" />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default AssetPickerDialog;
