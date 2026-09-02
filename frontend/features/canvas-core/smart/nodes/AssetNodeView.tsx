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
 *
 * Which files the checklist offers is NOT decided here — `../assetFiles`
 * owns that, and the factory seeds from the same module. They used to answer
 * it separately and disagreed, which selected a file the card would not draw.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ExternalLink, PackageX } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PRIMARY_SLOT } from '../../../../components/assets/assetSlots';
import {
  ASSET_TYPE_ICON,
  slotLabelKey,
  typeSingularKey,
} from '../../../../components/resources/assets/assetTypeMeta';
import { GeneratedApiError } from '../../../../services/apiEnvelope';
import {
  fetchAssetDetail,
  type AssetRowDetail,
} from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { fileVisibleUnderLoadout, orderedReferenceFiles } from '../assetFiles';
import { useCanvasScope } from '../canvasScope';
import { downstreamGenModel } from '../promptInputs';
import type { AssetNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useModelCapabilities } from './useModelCapabilities';
import { useNodeDataPatch } from './useNodeDataPatch';

/** Detail-fetch outcome. `error` is "could not ask", never "is not there". */
type LoadState = 'idle' | 'loading' | 'ready' | 'error';

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
  const selected_file_ids = useMemo(
    () => node.selected_file_ids ?? [],
    [node.selected_file_ids],
  );
  const primarySlot = PRIMARY_SLOT[assetType] ?? null;

  // ── The provider's reference ceiling (plan ruling E) ───────────────────
  // The ceiling belongs to the MODEL, and this card has none — the prompt it
  // feeds does. `downstreamGenModel` answers null when the card feeds two
  // prompts on different models, and `useModelCapabilities` answers null for
  // an unknown model, a still-loading fetch or an old backend. Both nulls mean
  // the same thing here: NO limit, render full support. Guessing a ceiling
  // would disable a file that would in fact have been sent.
  const downstreamNodes = useCanvasCoreStore((st) => st.nodes);
  const downstreamConnections = useCanvasCoreStore((st) => st.connections);
  const genModel = useMemo(
    () => downstreamGenModel(id, downstreamNodes as never, downstreamConnections as never),
    [id, downstreamNodes, downstreamConnections],
  );
  const caps = useModelCapabilities(genModel);
  const maxRefs = caps?.max_refs ?? null;
  // Rank among the CHECKED rows. `files` is ordered by
  // `referenceSlotPriority` — the mirror of the backend `_slot_priority` that
  // `reference_order` walks — so list position IS delivery position, and the
  // provider's cap trims this list's tail. Ranking by any other order (the
  // slot DECLARATION order, say) dims a file that is in fact sent.
  //
  // ⚠️ `max_refs` is a limit on the REQUEST, and this is one card. Two asset
  // cards feeding one prompt can each sit under the ceiling while the run
  // exceeds it; that overflow surfaces afterwards as `dropped_knobs: ['refs']`
  // on the prompt node. Nothing here promises a request-level guarantee.
  const selectedRank = useMemo(() => {
    const rank = new Map<string, number>();
    let i = 0;
    for (const f of files) {
      if (selected_file_ids.includes(f.resource_id)) rank.set(f.resource_id, i++);
    }
    return rank;
  }, [files, selected_file_ids]);
  const selectedCount = selectedRank.size;
  const atLimit = maxRefs !== null && selectedCount >= maxRefs;

  // What the last run's bundle would not send, and whether asking failed at
  // all. Empty is a real answer ("nothing dropped"); a null error is "the ask
  // worked". Only a non-empty one renders.
  const bundleDropped = useMemo(
    () => node.last_bundle_dropped ?? [],
    [node.last_bundle_dropped],
  );
  const bundleError = node.last_bundle_error ?? null;
  const droppedByReason = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of bundleDropped) {
      const reason = String(d?.reason || 'over_limit');
      counts.set(reason, (counts.get(reason) ?? 0) + 1);
    }
    return [...counts.entries()];
  }, [bundleDropped]);

  const onLoadoutChange = useCallback(
    (raw: string) => {
      const next = raw === '' ? null : raw;
      // Files pinned to the outfit being left are no longer applicable —
      // keeping them selected would ship another costume's references into
      // the next generation. Everything the user picked that still applies
      // survives, so this is a prune, not a reset.
      const current = liveAssetData(id)?.selected_file_ids ?? [];
      if (detail === null) {
        // The prune needs the file rows to know which selections belong to
        // the outfit being left. With none loaded, EVERY id looks
        // inapplicable and the whole selection would be wiped — and
        // `patchNode` writes no history entry, so there is no undo. Not
        // knowing is a reason to change nothing but the binding.
        patch({ loadout_id: next });
        return;
      }
      const stillApplies = new Set(
        detail.files
          .filter((f) => fileVisibleUnderLoadout(f, next))
          .map((f) => f.resource_id),
      );
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
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="text-[10px] font-medium uppercase tracking-wide text-canvas-muted">
            {t('canvas.asset.references', 'Reference Files')}
          </span>
          {/* What the LAST run's bundle would not send, grouped by reason. The
              backend is the authority on this — the greying below is only a
              hint given ahead of time. Absent/empty renders nothing: silence
              means everything checked was delivered, so it can never be a
              default. */}
          {droppedByReason.length > 0 && (
            <span
              data-testid="asset-node-dropped"
              title={bundleDropped
                .map((d) => `${d.resource_id} — ${d.reason}`)
                .join('\n')}
              className="shrink-0 rounded-full bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn"
            >
              {t('assets.node.refsDropped', {
                refs: droppedByReason
                  .map(([reason, count]) =>
                    t('assets.node.refsDroppedGroup', {
                      count,
                      // An unrecognised code still renders as itself — a badge
                      // that omits a reference because nobody wrote its label
                      // is the silent drop all over again.
                      reason: t(`assets.node.dropReason.${reason}`, reason),
                      defaultValue: '{{count}} ({{reason}})',
                    }),
                  )
                  .join(', '),
                defaultValue: 'Not sent: {{refs}}',
              })}
            </span>
          )}
        </div>

        {bundleError && (
          <div
            data-testid="asset-node-bundle-error"
            className="mb-1 text-[11px] text-warn"
            title={bundleError}
          >
            {t(
              'assets.node.bundleFailed',
              'The last run could not read this asset. Nothing from it was sent.',
            )}
          </div>
        )}

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
              // Past the ceiling: a CHECKED row beyond it will be trimmed by
              // the bundle, an UNCHECKED one cannot be added without pushing
              // something else out.
              const overLimit =
                checked && maxRefs !== null && (selectedRank.get(f.resource_id) ?? 0) >= maxRefs;
              // A checked row is never disabled — the user has to be able to
              // take it back off. Disabling it would trap the selection at a
              // ceiling with no way down, and `patchNode` writes no history
              // entry, so there is no undo either.
              const blocked = !checked && atLimit;
              const limitHint = t('assets.node.refsLimit', {
                count: maxRefs ?? 0,
                defaultValue:
                  'This model takes {{count}} reference images. The rest are not sent.',
              });
              return (
                <li key={f.resource_id}>
                  <label
                    data-testid={`asset-node-row-${f.resource_id}`}
                    data-over-limit={overLimit || blocked ? 'true' : undefined}
                    className={`nodrag flex items-center gap-1.5 ${
                      blocked || overLimit
                        ? 'cursor-not-allowed opacity-50'
                        : 'cursor-pointer'
                    }`}
                    title={blocked || overLimit ? limitHint : slotName}
                  >
                    <input
                      type="checkbox"
                      data-testid={`asset-node-file-${f.resource_id}`}
                      aria-label={t('canvas.asset.useFile', {
                        slot: slotName,
                        defaultValue: 'Use {{slot}} reference',
                      })}
                      checked={checked}
                      disabled={readOnly || blocked}
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

        {/* Said once, under the list, when the ceiling is actually in play.
            `maxRefs === null` is "unknown", not "zero" — it renders nothing. */}
        {maxRefs !== null && atLimit && files.length > 0 && (
          <div
            data-testid="asset-node-refs-limit"
            className="mt-1 text-[10px] text-canvas-muted"
          >
            {t('assets.node.refsLimit', {
              count: maxRefs,
              defaultValue:
                'This model takes {{count}} reference images. The rest are not sent.',
            })}
          </div>
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
