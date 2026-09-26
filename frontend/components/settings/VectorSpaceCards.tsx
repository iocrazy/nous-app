// components/settings/VectorSpaceCards.tsx
//
// The space-switching half of Settings → AI → Vectors: one card per
// CANDIDATE embedding space (fill it with its own model, switch to it once it
// covers the library, delete it) and the Add Space picker. State and requests
// live in VectorsPanel; these components only render and call back.

import { useState, type ReactNode } from 'react';

import type { BackfillResult, NousModelPublic, PlatformEngineState } from '../../types/api';
import { platformModelAvailability } from '../../utils/platformModel';
import type { VectorSpaceStatus } from '../../services/searchService';

type TFn = (key: string, opts?: Record<string, unknown>) => string;

const NUM = new Intl.NumberFormat('en-US');

const BTN =
  'rounded-lg border border-ink-700 px-2 py-0.5 text-xs text-ink-200 hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-50';
const DANGER_BTN =
  'rounded-lg border border-danger-line px-2 py-0.5 text-xs text-danger hover:bg-danger-soft disabled:cursor-not-allowed disabled:opacity-50';

export function SpaceRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-500">{label}</dt>
      <dd className="text-ink-200 break-all">{children}</dd>
    </>
  );
}

export function Capabilities({ modalities }: { modalities: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {modalities.map((m) => (
        <span
          key={m}
          data-testid={`vector-capability-${m}`}
          className="rounded border border-ok-line bg-ok-soft px-1.5 text-xs text-ok"
        >
          {m}
        </span>
      ))}
    </span>
  );
}

interface CandidateCardProps {
  space: VectorSpaceStatus;
  t: TFn;
  canManage: boolean;
  /** Another space action is in flight: every button waits. */
  busy: boolean;
  lastResult?: BackfillResult;
  formatResult: (r: BackfillResult) => string;
  onBackfill: (spaceId: string) => void;
  onSwitch: (spaceId: string) => void;
  onDelete: (spaceId: string) => void;
  /** Point the VISUAL layer (shot frames) at this space / back at the
   *  current one. Absent on backends before per-layer spaces: no buttons. */
  onUseForVisual?: (spaceId: string) => void;
  onFollowCurrent?: () => void;
}

export function CandidateCard({
  space,
  t,
  canManage,
  busy,
  lastResult,
  formatResult,
  onBackfill,
  onSwitch,
  onDelete,
  onUseForVisual,
  onFollowCurrent,
}: CandidateCardProps) {
  const [confirming, setConfirming] = useState(false);
  const semantic = space.layers.find((l) => l.layer === 'semantic');
  const visualLayer = space.layers.find((l) => l.layer === 'visual');
  const covered = semantic?.covered ?? 0;
  const total = semantic?.total ?? 0;
  const pct = total > 0 ? Math.min(100, (covered / total) * 100) : 0;
  const inCatalog = space.catalog_name !== null;
  const full = total > 0 && covered >= total;
  const adminOnly = t('settings.vectors.adminOnly');
  const isVisual = space.visual === true;
  const takesImages = space.modalities.includes('image');

  let switchHint: string | undefined;
  if (!canManage) switchHint = adminOnly;
  else if (!inCatalog) switchHint = t('settings.vectors.switchNeedsCatalog');
  else if (!full) switchHint = t('settings.vectors.switchNeedsFull');

  let visualHint: string | undefined;
  if (!canManage) visualHint = adminOnly;
  else if (!inCatalog) visualHint = t('settings.vectors.switchNeedsCatalog');
  else if (!takesImages) visualHint = t('settings.vectors.visualNeedsImage');

  let deleteHint: string | undefined;
  if (!canManage) deleteHint = adminOnly;
  else if (isVisual) deleteHint = t('settings.vectors.deleteServesVisual');

  return (
    <div
      data-testid={`vector-candidate-${space.id}`}
      className="rounded-lg border border-dashed border-ink-700 bg-ink-950/40 p-3 space-y-2"
    >
      <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-ink-500">
        {t('settings.vectors.candidateSpace')}
        {isVisual && (
          <span
            data-testid="vector-visual-badge"
            className="rounded border border-info/60 bg-info/10 px-1.5 py-px normal-case tracking-normal text-info"
          >
            {t('settings.vectors.visualLayerBadge')}
          </span>
        )}
      </p>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5 text-sm">
        <SpaceRow label={t('settings.vectors.provider')}>{space.actual_model}</SpaceRow>
        <SpaceRow label={t('settings.vectors.catalogModel')}>
          {inCatalog ? space.catalog_name : <span className="text-warn">{t('settings.vectors.notInCatalog')}</span>}
        </SpaceRow>
        <SpaceRow label={t('settings.vectors.protocol')}>{space.protocol}</SpaceRow>
        <SpaceRow label={t('settings.vectors.capabilities')}>
          <Capabilities modalities={space.modalities} />
        </SpaceRow>
        <SpaceRow label={t('settings.vectors.colCoverage')}>
          <span className="flex items-center gap-2">
            <span className="tabular-nums">{`${NUM.format(covered)} / ${NUM.format(total)}`}</span>
            <span className="h-1 w-24 overflow-hidden rounded bg-ink-800">
              <span className={`block h-full ${full ? 'bg-ok' : 'bg-info'}`} style={{ width: `${pct}%` }} />
            </span>
          </span>
        </SpaceRow>
        {visualLayer && (
          <SpaceRow label={t('settings.vectors.layerVisual')}>
            <span className="tabular-nums" data-testid="vector-candidate-visual-coverage">
              {`${NUM.format(visualLayer.covered)} / ${NUM.format(visualLayer.total)}`}
            </span>
          </SpaceRow>
        )}
      </dl>
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-danger">{t('settings.vectors.deleteConfirm')}</span>
          <button
            type="button"
            className={DANGER_BTN}
            disabled={busy}
            onClick={() => {
              setConfirming(false);
              onDelete(space.id);
            }}
          >
            {t('settings.vectors.confirmDelete')}
          </button>
          <button type="button" className={BTN} onClick={() => setConfirming(false)}>
            {t('settings.vectors.cancel')}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className={BTN}
            disabled={busy || !inCatalog}
            title={inCatalog ? undefined : t('settings.vectors.backfillNeedsCatalog')}
            onClick={() => onBackfill(space.id)}
          >
            {t('settings.vectors.backfill200')}
          </button>
          <button
            type="button"
            className={BTN}
            disabled={busy || switchHint !== undefined}
            title={switchHint}
            onClick={() => onSwitch(space.id)}
          >
            {t('settings.vectors.switchToSpace')}
          </button>
          {onUseForVisual && onFollowCurrent && (isVisual ? (
            <button
              type="button"
              className={BTN}
              disabled={busy || !canManage}
              title={canManage ? undefined : adminOnly}
              onClick={() => onFollowCurrent()}
            >
              {t('settings.vectors.followCurrent')}
            </button>
          ) : (
            <button
              type="button"
              className={BTN}
              disabled={busy || visualHint !== undefined}
              title={visualHint}
              onClick={() => onUseForVisual(space.id)}
            >
              {t('settings.vectors.useForVisual')}
            </button>
          ))}
          <button
            type="button"
            className={DANGER_BTN}
            disabled={busy || deleteHint !== undefined}
            title={deleteHint}
            onClick={() => setConfirming(true)}
          >
            {t('settings.vectors.deleteSpace')}
          </button>
        </div>
      )}
      {lastResult && (
        <p className="text-xs text-ink-400">
          <span className="text-ink-500">{t('settings.vectors.lastBackfill')} · </span>
          <span>{formatResult(lastResult)}</span>
        </p>
      )}
    </div>
  );
}

interface AddSpacePickerProps {
  t: TFn;
  /** null while the catalog list loads. */
  models: NousModelPublic[] | null;
  /** nous-engine reachability behind the list (null = not applicable). */
  engine?: PlatformEngineState | null;
  /** Catalog names that already have a space (active or candidate). */
  taken: ReadonlySet<string>;
  /** Catalog name being probed, if any. */
  probing: string | null;
  onPick: (model: NousModelPublic) => void;
  onClose: () => void;
}

export function AddSpacePicker({ t, models, engine = null, taken, probing, onPick, onClose }: AddSpacePickerProps) {
  const available = (models ?? []).filter((m) => !taken.has(m.name));
  return (
    <div
      data-testid="vector-add-space-picker"
      className="rounded-lg border border-ink-700 bg-ink-950/60 p-3 space-y-2"
    >
      <div className="flex items-center justify-between">
        <p className="text-xs text-ink-400">{t('settings.vectors.addSpacePick')}</p>
        <button type="button" className={BTN} onClick={onClose}>
          {t('settings.vectors.cancel')}
        </button>
      </div>
      {models === null && <p className="text-xs text-ink-500">{t('settings.vectors.addSpaceLoading')}</p>}
      {models !== null && engine?.reachable === false && (
        <p data-testid="vector-catalog-engine-unreachable" role="status" className="text-xs text-warn">
          {t('aiSettings.platformEngineUnreachable')}
        </p>
      )}
      {models !== null && engine?.reachable !== false && engine?.stale && (
        <p data-testid="vector-catalog-engine-stale" role="status" className="text-xs text-ink-400">
          {t('aiSettings.platformEngineStale')}
        </p>
      )}
      {models !== null && available.length === 0 && (
        <p className="text-xs text-ink-500">{t('settings.vectors.addSpaceNone')}</p>
      )}
      {available.length > 0 && (
        <ul className="space-y-1">
          {available.map((m) => {
            // Same rule as every platform picker (utils/platformModel): an
            // `idle` row (nous-engine: authorized, not loaded) is listed but
            // not pickable — the space's probe would get "not loaded" back.
            const { selectable } = platformModelAvailability(
              m.last_test_status === 'fail' ? null : m.last_test_status,
            );
            const statusLabel = !selectable
              ? t('platformModel.notLoaded')
              : m.last_test_status === 'not_probed'
                ? t('settings.vectors.modelStatusUnknown')
                : null;
            return (
              <li key={m.name}>
                <button
                  type="button"
                  disabled={probing !== null || !selectable}
                  onClick={() => onPick(m)}
                  data-model-name={m.name}
                  className="flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1 text-left text-sm text-ink-200 hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span>{m.display_name}</span>
                  <span className="flex items-baseline gap-2">
                    {statusLabel && (
                      <span data-testid="vector-catalog-model-status" className="text-[11px] text-warn">
                        {statusLabel}
                      </span>
                    )}
                    <span className="text-xs text-ink-500">{m.name}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {probing && (
        <p className="text-xs text-ink-400">{t('settings.vectors.addSpaceProbing', { model: probing })}</p>
      )}
    </div>
  );
}
