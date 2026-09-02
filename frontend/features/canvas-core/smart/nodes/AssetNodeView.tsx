/**
 * Asset-library reference card (P4 Task 4).
 *
 * A SOURCE card that POINTS at an `assets` row: cover + name + type chip +
 * readiness, a loadout picker for characters, and a checklist of the reference
 * files this card feeds downstream. The backend mirrors `data.asset_id` /
 * `data.loadout_id` into `canvas_asset_refs` on every save, which is what makes
 * "where is this asset used" answerable — see `AssetNodeData`.
 *
 * Three decisions worth stating, because each has an obvious wrong version:
 *
 *  * THE SNAPSHOT IS NEVER WRITTEN BACK. `data.name` / `cover_file_id` /
 *    `readiness_state` are what the card looked like when it was placed. The
 *    live detail overrides them for RENDERING only. Seeding node data from a
 *    fetched prop in an effect is the pattern that silently eats user edits
 *    (`reference-useeffect-prop-seed-swallows-edits`), and it would also make
 *    every canvas open dirty the document and schedule a PUT.
 *  * 404 IS THE ONLY THING THAT MEANS REMOVED. A network failure, a 403, a
 *    500 — those mean "could not ask", and answering them with an
 *    `Asset Removed` tombstone would write a permanent claim into
 *    `nodes_json` about something nobody checked. They render a retry-able
 *    load error instead, and the card keeps showing its snapshot.
 *  * OPEN SHEET IS A NEW TAB, NOT A ROUTER NAVIGATION. Leaving the canvas
 *    in place is the point; a viewer clicking through to the library should
 *    not lose their board. It is also why this file needs no Router context
 *    beyond `useParams`, which answers `{}` (not a throw) when there is none.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ExternalLink, PackageX } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PRIMARY_SLOT, slotsFor } from '../../../../components/assets/assetSlots';
import {
  ASSET_TYPE_ICON,
  slotLabelKey,
  typeSingularKey,
} from '../../../../components/resources/assets/assetTypeMeta';
import { GeneratedApiError } from '../../../../services/apiEnvelope';
import {
  fetchAssetDetail,
  type AssetFileRow,
  type AssetRowDetail,
} from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { useCanvasScope } from '../canvasScope';
import type { AssetNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

/** Detail-fetch outcome. `error` is "could not ask", never "is not there". */
type LoadState = 'idle' | 'loading' | 'ready' | 'error';

/**
 * The files this card may reference, primary slot first then the type's other
 * slots in table order, then anything in a slot the table does not name.
 *
 * Files pinned to a DIFFERENT loadout are dropped: a loadout-scoped file
 * belongs to that outfit alone, and offering it under another one would let a
 * user hand the generator the wrong costume. Files with no loadout are shared
 * and always shown.
 *
 * Exported for its test — the ordering and the loadout filter are the parts
 * worth pinning, not the markup around them.
 */
export function orderedReferenceFiles(
  files: readonly AssetFileRow[],
  assetType: AssetNodeData['asset_type'],
  loadoutId: string | null,
): AssetFileRow[] {
  const order = slotsFor(assetType);
  const rank = (slot: string): number => {
    const i = order.indexOf(slot);
    return i === -1 ? order.length : i;
  };
  return files
    .filter((f) => f.loadout_id === null || f.loadout_id === loadoutId)
    .slice()
    .sort((a, b) => rank(a.slot) - rank(b.slot) || a.sort_order - b.sort_order);
}

/**
 * The node's data AS THE STORE HOLDS IT RIGHT NOW.
 *
 * Every read-modify-write below (toggle a file, switch a loadout and prune
 * the selection) must start from this, not from the `data` prop. React Flow
 * re-renders with fresh data in the app, but a callback that closes over the
 * render's copy is one stale prop away from computing the next array from a
 * previous one — the second click then undoes the first. Reading the store
 * makes the arithmetic correct by construction instead of by timing.
 */
function liveAssetData(id: string): AssetNodeData | null {
  const node = useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id?: unknown }).id === id) as
    | { data?: AssetNodeData }
    | undefined;
  return node?.data ?? null;
}

export function AssetNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as AssetNodeData;
  const { t } = useTranslation();
  const patch = useNodeDataPatch(id);
  const readOnly = useCanvasReadOnly();
  const { scopeId, resPath } = useCanvasScope();

  const [detail, setDetail] = useState<AssetRowDetail | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('idle');

  const assetId = node.asset_id;
  const removed = node.removed === true;

  useEffect(() => {
    // No scope in the URL = nothing to ask with; an empty `scope_id` is a 403
    // `not_a_member`, not an unscoped query. A tombstoned card asks nothing.
    if (!scopeId || !assetId || removed) return;
    let cancelled = false;
    setLoadState('loading');
    fetchAssetDetail(scopeId, assetId)
      .then((row) => {
        if (cancelled) return;
        setDetail(row);
        setLoadState('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AssetNodeView] fetchAssetDetail failed:', err);
        if (err instanceof GeneratedApiError && err.status === 404) {
          // The one branch that means the asset is gone. Persisted, so the
          // card keeps saying so after a reload without re-asking.
          patch({ removed: true });
          return;
        }
        setLoadState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, assetId, removed, patch]);

  // Live row wins for DISPLAY; the snapshot is the fallback, never overwritten.
  const name = detail?.name ?? node.name;
  const assetType = detail?.asset_type ?? node.asset_type;
  const coverFileId = detail?.cover_file_id ?? node.cover_file_id;
  const readinessState = detail?.readiness?.state ?? node.readiness_state;
  const ready = readinessState === 'ready';

  const loadouts = detail?.loadouts ?? [];
  const files = useMemo(
    () => orderedReferenceFiles(detail?.files ?? [], assetType, node.loadout_id),
    [detail, assetType, node.loadout_id],
  );
  const selected_file_ids = node.selected_file_ids ?? [];
  const primarySlot = PRIMARY_SLOT[assetType] ?? null;

  const onLoadoutChange = useCallback(
    (raw: string) => {
      const next = raw === '' ? null : raw;
      // Files pinned to the outfit being left are no longer applicable —
      // keeping them selected would ship another costume's references into
      // the next generation. Everything the user picked that still applies
      // survives, so this is a prune, not a reset.
      const stillApplies = new Set(
        (detail?.files ?? [])
          .filter((f) => f.loadout_id === null || f.loadout_id === next)
          .map((f) => f.resource_id),
      );
      const current = liveAssetData(id)?.selected_file_ids ?? [];
      patch({
        loadout_id: next,
        selected_file_ids: current.filter((rid) => stillApplies.has(rid)),
      });
    },
    [patch, detail, id],
  );

  const toggleFile = useCallback(
    (resourceId: string) => {
      const current = liveAssetData(id)?.selected_file_ids ?? [];
      patch({
        selected_file_ids: current.includes(resourceId)
          ? current.filter((rid) => rid !== resourceId)
          : [...current, resourceId],
      });
    },
    [patch, id],
  );

  const Icon = ASSET_TYPE_ICON[assetType] ?? ASSET_TYPE_ICON.prop;
  const sheetHref = resPath(`/resources/assets/item/${assetId}`);

  if (removed) {
    return (
      <div
        data-testid="smart-asset-node"
        data-asset-removed="true"
        className={`mh-node border-danger-line ${selected ? 'mh-node-selected' : ''}`}
        style={{ width: SMART_NODE_DEFAULT_WIDTH.asset }}
      >
        <div className="mh-node-head">
          <div className="mh-node-title">
            {t('canvas.asset.title', 'Asset')}
          </div>
        </div>
        <div
          data-testid="asset-node-removed"
          className="flex items-center gap-2.5 p-3 text-canvas-muted"
        >
          <PackageX size={20} className="shrink-0 text-danger" />
          <div className="min-w-0">
            <div className="truncate text-[13px] font-semibold text-danger">
              {t('canvas.asset.removed', 'Asset Removed')}
            </div>
            <div className="truncate text-[11px]" title={node.name}>
              {node.name}
            </div>
          </div>
        </div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  return (
    <div
      data-testid="smart-asset-node"
      data-asset-id={assetId}
      data-loadout-id={node.loadout_id ?? ''}
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.asset }}
    >
      <div className="mh-node-head">
        <div className="mh-node-title">{t('canvas.asset.title', 'Asset')}</div>
        <span
          data-testid="asset-node-type-chip"
          className="rounded-full bg-canvas-line/40 px-2 py-0.5 text-[10px] font-medium text-canvas-muted"
        >
          {t(typeSingularKey(assetType), assetType)}
        </span>
      </div>

      <div className="flex gap-2.5 p-3 pb-2">
        <div className="h-20 w-16 shrink-0 overflow-hidden rounded-lg border border-canvas-line bg-canvas-line/20">
          {coverFileId ? (
            <img
              data-testid="asset-node-cover"
              src={getResourceCoverUrl(coverFileId)}
              alt={name}
              className="h-full w-full object-cover"
            />
          ) : (
            <div
              data-testid="asset-node-cover-fallback"
              className="flex h-full w-full items-center justify-center text-canvas-muted"
            >
              <Icon size={22} />
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div
            className="truncate text-[13px] font-semibold text-ink-100"
            title={name}
          >
            {name}
          </div>

          <span
            data-testid="asset-node-readiness"
            data-readiness={readinessState}
            className={`mt-1 inline-block rounded-full border px-1.5 text-[10px] font-medium ${
              ready
                ? 'border-ok-line bg-ok-soft text-ok'
                : 'border-warn-line bg-warn-soft text-warn'
            }`}
          >
            {ready
              ? t('assets.readiness.ready', 'Ready')
              : t('assets.readiness.draft', 'Draft')}
          </span>

          {assetType === 'character' && (
            <select
              data-testid="asset-node-loadout"
              aria-label={t('canvas.asset.loadout', 'Asset loadout')}
              className="nodrag mt-1.5 w-full rounded border border-canvas-line bg-canvas-card px-1.5 py-1 text-[11px] text-canvas-text outline-none disabled:opacity-60"
              value={node.loadout_id ?? ''}
              disabled={readOnly}
              onChange={(e) => onLoadoutChange(e.target.value)}
            >
              <option value="">
                {t('canvas.asset.loadoutNone', 'No loadout')}
              </option>
              {loadouts.map((lo) => (
                <option key={lo.id} value={lo.id}>
                  {lo.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Reference files. The whole point of the card is choosing WHICH ones
          go downstream, so the empty and the failed cases both say what is
          going on rather than rendering as an empty box. */}
      <div className="border-t border-canvas-line px-3 py-2">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-canvas-muted">
          {t('canvas.asset.references', 'Reference Files')}
        </div>

        {loadState === 'error' ? (
          <div data-testid="asset-node-load-error" className="text-[11px] text-warn">
            {t('canvas.asset.loadFailed', 'Could not load this asset')}
          </div>
        ) : files.length === 0 ? (
          <div data-testid="asset-node-no-files" className="text-[11px] text-canvas-muted">
            {loadState === 'loading'
              ? t('canvas.asset.loading', 'Loading…')
              : t('canvas.asset.noFiles', 'No reference files')}
          </div>
        ) : (
          <ul
            data-testid="asset-node-files"
            className="nowheel max-h-36 space-y-1 overflow-y-auto"
          >
            {files.map((f) => {
              const checked = selected_file_ids.includes(f.resource_id);
              const slotName = t(slotLabelKey(f.slot), f.slot);
              return (
                <li key={`${f.resource_id}-${f.slot}`}>
                  <label
                    className="nodrag flex cursor-pointer items-center gap-1.5"
                    title={slotName}
                  >
                    <input
                      type="checkbox"
                      data-testid={`asset-node-file-${f.resource_id}`}
                      aria-label={t('canvas.asset.useFile', {
                        slot: slotName,
                        defaultValue: 'Use {{slot}} reference',
                      })}
                      checked={checked}
                      disabled={readOnly}
                      onChange={() => toggleFile(f.resource_id)}
                      className="h-3 w-3 shrink-0"
                    />
                    <img
                      src={getResourceCoverUrl(f.resource_id)}
                      alt=""
                      className="h-6 w-6 shrink-0 rounded border border-canvas-line object-cover"
                    />
                    <span className="min-w-0 flex-1 truncate text-[11px] text-canvas-text">
                      {slotName}
                    </span>
                    {f.slot === primarySlot && (
                      <span className="shrink-0 text-[9px] uppercase text-canvas-muted">
                        {t('canvas.asset.primary', 'Primary')}
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-t border-canvas-line px-3 py-1.5">
        <a
          data-testid="asset-node-open-sheet"
          className="nodrag inline-flex items-center gap-1 text-[11px] text-canvas-muted hover:text-[var(--accent-text)]"
          href={sheetHref}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink size={11} />
          {t('canvas.asset.openSheet', 'Open Sheet')}
        </a>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export default AssetNodeView;
