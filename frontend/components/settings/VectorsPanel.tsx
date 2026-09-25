// components/settings/VectorsPanel.tsx
//
// Settings → AI → Vectors. Two sections:
//   • Vector Spaces — the active embedding space (model / protocol /
//     capabilities / dimensions / instruction) from GET /search/vectors/status,
//     plus one card per candidate space (VectorSpaceCards): Add Space probes a
//     catalog embedding model and creates its space, Backfill 200 fills it with
//     its own model, Switch To This Space makes it active once it covers the
//     library, Delete removes it (inline confirmation). Add / Switch / Delete
//     are admin-only (`can_manage`); filling one's own coverage is not.
//   • Retrieval Layers — per-layer coverage of the ACTIVE space, with the
//     semantic layer's backfill (Dry Run / Run 200) wired to
//     POST /ai/analyze/backfill-embeddings.
//
// Visual / Camera rows are DELIBERATE disabled placeholders (they arrive with
// shot indexing), the same way the asset page's `Send To Canvas` is.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { backfillEmbeddings } from '../../services/aiService';
import type { BackfillResult } from '../../types/api';
import { ApiError } from '../../services/apiClient';
import {
  activateVectorSpace,
  createVectorSpace,
  deleteVectorSpace,
  getVectorSpaceCatalog,
  getVectorsStatus,
  type VectorsStatus,
} from '../../services/searchService';
import type { NousModelPublic } from '../../types';
import { AddSpacePicker, CandidateCard, Capabilities, SpaceRow } from './VectorSpaceCards';

// Same ceiling as the backend (BackfillEmbeddingsBody.limit le=200).
const BACKFILL_BATCH = 200;
const NUM = new Intl.NumberFormat('en-US');

type TFn = (key: string, opts?: Record<string, unknown>) => string;
type LoadState = 'loading' | 'ready' | 'failed';

/** The typed body of the production ErrorResponse envelope (`details`). */
function typedDetails(err: unknown): Record<string, unknown> | undefined {
  if (!(err instanceof ApiError)) return undefined;
  const details = err.details;
  return details && typeof details === 'object' ? (details as Record<string, unknown>) : undefined;
}

/** Typed refusal code from the production ErrorResponse envelope
 *  (`details.code`), never the generic `http_<status>` top-level code. */
function typedErrorCode(err: unknown): string | undefined {
  const code = typedDetails(err)?.code;
  return typeof code === 'string' ? code : undefined;
}

/** One line for a failed space action (Add / Switch / Delete / candidate
 *  backfill), from the typed code when there is one. */
function spaceErrorLine(err: unknown, t: TFn, model?: string): string {
  const details = typedDetails(err) ?? {};
  switch (typedErrorCode(err)) {
    case 'dimension_mismatch':
      return t('settings.vectors.errorDimensionMismatch', {
        model: details.model ?? model ?? '',
        got: details.got,
        expected: details.expected,
      });
    case 'provider_error':
      return t('settings.vectors.errorProbe', { model: details.model ?? model ?? '' });
    case 'space_active':
      return t('settings.vectors.errorSpaceActive');
    case 'space_catalog_row_missing':
      return t('settings.vectors.switchNeedsCatalog');
    case 'catalog_model_disabled':
      return t('settings.vectors.errorModelDisabled');
    case 'not_an_embedding_model':
      return t('settings.vectors.errorNotEmbedding');
    case 'space_not_found':
      return t('settings.vectors.errorSpaceNotFound');
    case 'vector_store_missing':
      return t('settings.vectors.errorSpaceStoreMissing');
    case 'byok_row_not_allowed':
      return t('settings.vectors.errorByokRow');
    case 'active_space_unknown':
      return t('settings.vectors.errorActiveUnknown');
    default:
      return errorLine(err, t);
  }
}

function errorLine(err: unknown, t: TFn): string {
  const code = typedErrorCode(err);
  if (code === 'embedder_unconfigured') return t('settings.vectors.errorEmbedderUnconfigured');
  if (code === 'vector_store_missing') return t('settings.vectors.errorStoreMissing');
  const message = err instanceof Error ? err.message : String(err);
  return t('settings.vectors.errorGeneric', { message });
}

function skippedReasons(skipped: BackfillResult['skipped']): string {
  const counts = new Map<string, number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  return Array.from(counts, ([reason, n]) => `${reason} ×${n}`).join(', ');
}

function formatBackfillResult(r: BackfillResult, t: TFn): string {
  const parts: string[] = [];
  const count = (n: number) => ({ count: NUM.format(n) });
  if (r.dry_run) {
    parts.push(t('settings.vectors.resultWouldEmbed', count(r.reembedded.length)));
    if (r.dispatched.length) parts.push(t('settings.vectors.resultWouldDispatch', count(r.dispatched.length)));
  } else {
    parts.push(t('settings.vectors.resultEmbedded', count(r.reembedded.length)));
    if (r.rehashed) parts.push(t('settings.vectors.resultRehashed', count(r.rehashed)));
    if (r.dispatched.length) parts.push(t('settings.vectors.resultDispatched', count(r.dispatched.length)));
  }
  if (r.skipped.length) {
    parts.push(
      t('settings.vectors.resultSkipped', {
        count: NUM.format(r.skipped.length),
        reasons: skippedReasons(r.skipped),
      }),
    );
  }
  if (r.in_flight) parts.push(t('settings.vectors.resultInFlight', count(r.in_flight)));
  parts.push(t('settings.vectors.resultRemaining', count(r.remaining)));
  return parts.join(' · ');
}

export function VectorsPanel() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<VectorsStatus | null>(null);
  // True when the backend predates GET /search/vectors/status (404): the space
  // is unknown, but the backfill endpoint has shipped longer and still works.
  const [statusEndpointMissing, setStatusEndpointMissing] = useState(false);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<BackfillResult | null>(null);
  const [backfillError, setBackfillError] = useState<string | null>(null);
  // Space switching: one action at a time; its outcome is one line.
  const [spaceBusy, setSpaceBusy] = useState(false);
  const [spaceError, setSpaceError] = useState<string | null>(null);
  const [spaceNotice, setSpaceNotice] = useState<string | null>(null);
  const [candidateResults, setCandidateResults] = useState<Record<string, BackfillResult>>({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerModels, setPickerModels] = useState<NousModelPublic[] | null>(null);
  const [probing, setProbing] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const next = await getVectorsStatus();
      setStatus(next);
      setStatusEndpointMissing(false);
      setLoadState('ready');
    } catch (err) {
      console.error('VectorsPanel: failed to load vector status', err);
      if (typedErrorCode(err) === 'vector_store_missing') {
        setStatus({ status: 'store_missing', space: null, layers: [] });
        setLoadState('ready');
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setStatus({ status: 'unconfigured', space: null, layers: [] });
        setStatusEndpointMissing(true);
        setLoadState('ready');
        return;
      }
      setLoadState((prev) => (prev === 'ready' ? prev : 'failed'));
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const runBackfill = async (dryRun: boolean) => {
    setBusy(true);
    setBackfillError(null);
    try {
      const result = await backfillEmbeddings({ limit: BACKFILL_BATCH, dry_run: dryRun });
      setLastResult(result);
      if (!dryRun) await loadStatus();
    } catch (err) {
      console.error('VectorsPanel: backfill failed', err);
      setBackfillError(errorLine(err, t));
    } finally {
      setBusy(false);
    }
  };

  /** Run one space action; its failure becomes the section's error line. */
  const spaceAction = async (fn: () => Promise<void>, model?: string) => {
    setSpaceBusy(true);
    setSpaceError(null);
    setSpaceNotice(null);
    try {
      await fn();
    } catch (err) {
      console.error('VectorsPanel: space action failed', err);
      setSpaceError(spaceErrorLine(err, t, model));
    } finally {
      setSpaceBusy(false);
    }
  };

  const openPicker = async () => {
    setPickerOpen(true);
    setPickerModels(null);
    setSpaceError(null);
    try {
      setPickerModels(await getVectorSpaceCatalog());
    } catch (err) {
      console.error('VectorsPanel: failed to load embedding models', err);
      setPickerModels([]);
    }
  };

  const addSpace = (model: NousModelPublic) =>
    spaceAction(async () => {
      setProbing(model.name);
      try {
        await createVectorSpace(model.name);
      } finally {
        setProbing(null);
      }
      setPickerOpen(false);
      setSpaceNotice(t('settings.vectors.spaceAdded', { model: model.display_name }));
      await loadStatus();
    }, model.name);

  const backfillCandidate = (spaceId: string) =>
    spaceAction(async () => {
      const result = await backfillEmbeddings({ limit: BACKFILL_BATCH, dry_run: false, space_id: spaceId });
      setCandidateResults((prev) => ({ ...prev, [spaceId]: result }));
      await loadStatus();
    });

  const switchTo = (spaceId: string) =>
    spaceAction(async () => {
      setStatus(await activateVectorSpace(spaceId));
      setSpaceNotice(t('settings.vectors.spaceSwitched'));
    });

  const removeSpace = (spaceId: string) =>
    spaceAction(async () => {
      const out = await deleteVectorSpace(spaceId);
      setCandidateResults((prev) => {
        const next = { ...prev };
        delete next[spaceId];
        return next;
      });
      setSpaceNotice(t('settings.vectors.spaceDeleted', { count: NUM.format(out.deleted_vectors) }));
      await loadStatus();
    });

  if (loadState === 'loading') {
    return <p className="text-sm text-ink-400">{t('settings.vectors.loading')}</p>;
  }
  if (loadState === 'failed' || !status) {
    return <p className="text-sm text-danger">{t('settings.vectors.loadFailed')}</p>;
  }

  return (
    <div className="space-y-6" data-testid="vectors-panel">
      <SpaceSection
        status={status}
        t={t}
        endpointMissing={statusEndpointMissing}
        busy={spaceBusy}
        error={spaceError}
        notice={spaceNotice}
        candidateResults={candidateResults}
        pickerOpen={pickerOpen}
        pickerModels={pickerModels}
        probing={probing}
        onOpenPicker={() => void openPicker()}
        onClosePicker={() => setPickerOpen(false)}
        onAdd={(m) => void addSpace(m)}
        onBackfill={(id) => void backfillCandidate(id)}
        onSwitch={(id) => void switchTo(id)}
        onDelete={(id) => void removeSpace(id)}
      />
      <LayersSection
        status={status}
        endpointMissing={statusEndpointMissing}
        t={t}
        busy={busy}
        onBackfill={runBackfill}
        lastResult={lastResult}
        backfillError={backfillError}
      />
    </div>
  );
}

interface SpaceSectionProps {
  status: VectorsStatus;
  t: TFn;
  endpointMissing: boolean;
  busy: boolean;
  error: string | null;
  notice: string | null;
  candidateResults: Record<string, BackfillResult>;
  pickerOpen: boolean;
  pickerModels: NousModelPublic[] | null;
  probing: string | null;
  onOpenPicker: () => void;
  onClosePicker: () => void;
  onAdd: (model: NousModelPublic) => void;
  onBackfill: (spaceId: string) => void;
  onSwitch: (spaceId: string) => void;
  onDelete: (spaceId: string) => void;
}

function SpaceSection({
  status,
  t,
  endpointMissing,
  busy,
  error,
  notice,
  candidateResults,
  pickerOpen,
  pickerModels,
  probing,
  onOpenPicker,
  onClosePicker,
  onAdd,
  onBackfill,
  onSwitch,
  onDelete,
}: SpaceSectionProps) {
  const space = status.space;
  const canManage = status.can_manage === true;
  const spaces = useMemo(() => status.spaces ?? [], [status.spaces]);
  const candidates = spaces.filter((s) => !s.active);
  // Catalog names that already have a space: the picker hides them.
  const taken = useMemo(
    () => new Set(spaces.map((s) => s.catalog_name).filter((n): n is string => n !== null)),
    [spaces],
  );
  const storeReady = !endpointMissing && status.status !== 'store_missing';
  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink-100">{t('settings.vectors.title')}</h3>
        <button
          type="button"
          disabled={!canManage || !storeReady || busy || pickerOpen}
          title={canManage ? undefined : t('settings.vectors.adminOnly')}
          onClick={onOpenPicker}
          className="rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-ink-200 hover:bg-ink-800 disabled:cursor-not-allowed disabled:border-ink-800 disabled:text-ink-500"
        >
          {t('settings.vectors.addSpace')}
        </button>
      </div>
      <div className="rounded-lg border border-ink-800 bg-ink-950/40 p-3">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-500">
          {t('settings.vectors.currentSpace')}
        </p>
        {endpointMissing && (
          <p className="text-sm text-warn">{t('settings.vectors.statusUnavailable')}</p>
        )}
        {!endpointMissing && status.status === 'unconfigured' && (
          <p className="text-sm text-warn">{t('settings.vectors.unconfigured')}</p>
        )}
        {status.status === 'store_missing' && (
          <p className="text-sm text-warn">{t('settings.vectors.storeMissing')}</p>
        )}
        {status.status === 'ok' && space && (
          <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5 text-sm">
            <SpaceRow label={t('settings.vectors.provider')}>{space.actual_model}</SpaceRow>
            <SpaceRow label={t('settings.vectors.protocol')}>{space.protocol}</SpaceRow>
            <SpaceRow label={t('settings.vectors.capabilities')}>
              <Capabilities modalities={space.modalities} />
            </SpaceRow>
            <SpaceRow label={t('settings.vectors.dimensions')}>
              {t('settings.vectors.dimensionsValue', { dims: space.dims })}
            </SpaceRow>
            <SpaceRow label={t('settings.vectors.instruction')}>{space.instruction_version}</SpaceRow>
          </dl>
        )}
      </div>
      {pickerOpen && (
        <AddSpacePicker
          t={t}
          models={pickerModels}
          taken={taken}
          probing={probing}
          onPick={onAdd}
          onClose={onClosePicker}
        />
      )}
      {candidates.map((c) => (
        <CandidateCard
          key={c.id}
          space={c}
          t={t}
          canManage={canManage}
          busy={busy}
          lastResult={candidateResults[c.id]}
          formatResult={(r) => formatBackfillResult(r, t)}
          onBackfill={onBackfill}
          onSwitch={onSwitch}
          onDelete={onDelete}
        />
      ))}
      {notice && (
        <p className="text-xs text-ok" data-testid="vector-space-notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="text-xs text-danger" data-testid="vector-space-error">
          {error}
        </p>
      )}
    </section>
  );
}

interface LayersSectionProps {
  status: VectorsStatus;
  endpointMissing: boolean;
  t: TFn;
  busy: boolean;
  onBackfill: (dryRun: boolean) => void;
  lastResult: BackfillResult | null;
  backfillError: string | null;
}

function LayersSection({
  status,
  endpointMissing,
  t,
  busy,
  onBackfill,
  lastResult,
  backfillError,
}: LayersSectionProps) {
  const semantic = status.layers.find((l) => l.layer === 'semantic');
  const transcript = status.layers.find((l) => l.layer === 'transcript');
  const total = semantic?.total ?? transcript?.total;
  const totalText = total === undefined ? '—' : NUM.format(total);
  // Unknown status (endpoint missing) is not a reason to block: the backfill
  // endpoint answers with a typed error if the embedder really is missing.
  const spaceReady = status.status === 'ok' || endpointMissing;
  const canBackfill = spaceReady && !busy;
  const disabledHint = spaceReady ? undefined : t('settings.vectors.backfillDisabledHint');
  const btn =
    'rounded-lg border border-ink-700 px-2 py-0.5 text-xs text-ink-200 hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-ink-100">{t('settings.vectors.layersTitle')}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-ink-500">
            <tr>
              <th className="py-1.5 pr-3 font-medium">{t('settings.vectors.colLayer')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('settings.vectors.colStatus')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('settings.vectors.colCoverage')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('settings.vectors.colSource')}</th>
              <th className="py-1.5 font-medium">{t('settings.vectors.colBackfill')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800 text-ink-200">
            <tr data-testid="vector-layer-semantic">
              <td className="py-2 pr-3">{t('settings.vectors.layerSemantic')}</td>
              <td className="py-2 pr-3">
                <StatusCell
                  ok={semantic?.status === 'ok'}
                  label={t(semantic?.status === 'ok' ? 'settings.vectors.statusOk' : 'settings.vectors.statusNotBuilt')}
                />
              </td>
              <td className="py-2 pr-3">
                <Coverage covered={semantic?.covered} total={semantic?.total} stale={semantic?.stale} t={t} />
              </td>
              <td className="py-2 pr-3 text-xs text-ink-400">{t('settings.vectors.semanticSource')}</td>
              <td className="py-2">
                <span className="flex gap-1.5">
                  <button
                    type="button"
                    className={btn}
                    disabled={!canBackfill}
                    title={disabledHint}
                    onClick={() => onBackfill(true)}
                  >
                    {t('settings.vectors.dryRun')}
                  </button>
                  <button
                    type="button"
                    className={btn}
                    disabled={!canBackfill}
                    title={disabledHint}
                    onClick={() => onBackfill(false)}
                  >
                    {t('settings.vectors.run200')}
                  </button>
                </span>
              </td>
            </tr>
            <PlaceholderRow id="visual" label={t('settings.vectors.layerVisual')} t={t} totalText={totalText} />
            <PlaceholderRow id="camera" label={t('settings.vectors.layerCamera')} t={t} totalText={totalText} />
            <tr data-testid="vector-layer-transcript">
              <td className="py-2 pr-3">{t('settings.vectors.layerTranscript')}</td>
              <td className="py-2 pr-3">
                <StatusCell ok={false} label={t('settings.vectors.transcriptPhase2')} />
              </td>
              <td className="py-2 pr-3">
                <Coverage covered={transcript?.covered} total={transcript?.total} />
              </td>
              <td className="py-2 pr-3" />
              <td className="py-2" />
            </tr>
          </tbody>
        </table>
      </div>
      {lastResult && (
        <p className="text-xs text-ink-400">
          <span className="text-ink-500">{t('settings.vectors.lastBackfill')} · </span>
          <span>{formatBackfillResult(lastResult, t)}</span>
        </p>
      )}
      {backfillError && (
        <p className="text-xs text-danger" data-testid="vectors-backfill-error">
          {backfillError}
        </p>
      )}
    </section>
  );
}

function PlaceholderRow({ id, label, t, totalText }: { id: string; label: string; t: TFn; totalText: string }) {
  return (
    <tr data-testid={`vector-layer-${id}`} className="text-ink-500">
      <td className="py-2 pr-3">{label}</td>
      <td className="py-2 pr-3">
        <StatusCell ok={false} label={t('settings.vectors.arrivesWithPr3')} />
      </td>
      <td className="py-2 pr-3 tabular-nums">{`— / ${totalText}`}</td>
      <td className="py-2 pr-3" />
      <td className="py-2" />
    </tr>
  );
}

function StatusCell({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className={`h-2 w-2 rounded-full ${ok ? 'bg-ok' : 'bg-ink-600'}`} />
      {label}
    </span>
  );
}

interface CoverageProps {
  covered?: number;
  total?: number;
  /** Covered vectors the next backfill re-embeds (older document version, or
   *  a summary / transcript newer than the vector). */
  stale?: number;
  t?: TFn;
}

function Coverage({ covered, total, stale, t }: CoverageProps) {
  if (covered === undefined || total === undefined) {
    return <span className="tabular-nums text-ink-500">—</span>;
  }
  const pct = total > 0 ? Math.min(100, (covered / total) * 100) : 0;
  return (
    <span className="flex flex-col gap-1">
      <span className="tabular-nums">
        {`${NUM.format(covered)} / ${NUM.format(total)}`}
        {stale && t ? (
          <span className="text-xs text-warn">
            {' '}
            {t('settings.vectors.staleSuffix', { count: NUM.format(stale) })}
          </span>
        ) : null}
      </span>
      <span className="h-1 w-24 overflow-hidden rounded bg-ink-800">
        <span className="block h-full bg-ok" style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}
