// frontend/components/VideoDetailPanel/ShotsTab.tsx
//
// The Shots tab (PR 3): one video's shot index in three states.
//   A  not indexed — what indexing would produce + Index This Video
//   B  indexing    — the index_shots task's progress (Task Center realtime)
//   C  indexed     — index facts, 2×2 numbers, shot strip, shot list
// Shared by VideoDetailPanel (My Downloads) and ResourceDetailPage.
//
// The tab FOLLOWS a task rather than polling: after a 202 it remembers the
// task id and reads that row from the Task Center context; completion
// re-reads GET /resources/{id}/shots, failure shows the typed reason.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clapperboard, Loader2, RefreshCw } from 'lucide-react';

import { useTaskManager, type UnifiedTask } from '../../contexts/TaskManagerContext';
import { getResourceShots, indexShots, shotErrorCode } from '../../services/shotsService';
import type { ShotOut, ShotsResponse } from '../../types/api';

/** Average shot length the estimate assumes, in seconds. */
const SECONDS_PER_SHOT = 4.5;
/** Each shot is embedded once — its keyframe (Visual). Clip vectors (Camera)
 *  arrive with PR 4 and will make this 2. */
const EMBEDDINGS_PER_SHOT = 1;
const ACTIVE: ReadonlySet<string> = new Set(['pending', 'processing']);

/** ≈ number of shots in a video of `durationSeconds`; null when unknown. */
export function estimateShots(durationSeconds?: number | null): number | null {
  if (durationSeconds == null || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return null;
  }
  return Math.ceil(durationSeconds / SECONDS_PER_SHOT);
}

/** m:ss of a millisecond offset. */
export function formatClockMs(ms: number): string {
  const whole = Math.floor(ms / 1000);
  return `${Math.floor(whole / 60)}:${(whole % 60).toString().padStart(2, '0')}`;
}

type Translate = (key: string, def: string, opts?: Record<string, unknown>) => string;

/** User-facing line for a typed refusal of the index call (codes from
 *  `POST /ai/analyze/index-shots/{id}` and the workflow's `error_code`). */
function refusalLine(code: string | undefined, fallback: string, t: Translate): string {
  switch (code) {
    case 'provider_no_image':
      return t(
        'detail.shots.errors.providerNoImage',
        'The current embedding model cannot take images. Pick an image-capable model in Settings → AI → Vectors.',
      );
    case 'embedder_unconfigured':
      return t(
        'detail.shots.errors.embedderUnconfigured',
        'No embedding model configured. Set one in Admin → AI Models.',
      );
    case 'vector_store_missing':
    case 'store_missing':
      return t(
        'detail.shots.errors.storeMissing',
        'The shot index is not ready yet (a database update is rolling out). Try again shortly.',
      );
    case 'not_a_video':
      return t('detail.shots.errors.notAVideo', 'Only videos can be cut into shots.');
    case 'no_video_file':
      return t('detail.shots.errors.noVideoFile', 'This video has no file to cut yet. Download it first.');
    case 'provider_error':
      return t('detail.shots.errors.providerError', 'The embedding provider kept failing. Try again later.');
    default:
      return fallback;
  }
}

/** Does `shot` contain the millisecond `ms`? */
const contains = (shot: ShotOut, ms: number): boolean => shot.start_ms <= ms && ms < shot.end_ms;

export interface ShotsTabProps {
  /** The `resources` row (string Snowflake). Absent while the download has
   *  no resource yet — the tab then explains instead of guessing. */
  resourceId?: string;
  durationSeconds?: number | null;
  /** Player position in seconds — highlights the shot being played. */
  currentTimeSeconds?: number;
  onSeek?: (seconds: number) => void;
  /** The matched shot's start when the panel was opened from a visual search
   *  hit: that shot is outlined in the strip and marked in the list. */
  hitStartMs?: number;
}

export const ShotsTab: React.FC<ShotsTabProps> = ({
  resourceId,
  durationSeconds,
  currentTimeSeconds,
  onSeek,
  hitStartMs,
}) => {
  const { t } = useTranslation();
  const tr = t as unknown as Translate;
  const { tasks, cancelTask } = useTaskManager();
  const [data, setData] = useState<ShotsResponse | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Task this tab follows (dispatched here, or found running on mount).
  const [watchedId, setWatchedId] = useState<string | null>(null);
  // Between the 202 and the task row reaching the Task Center context.
  const [optimistic, setOptimistic] = useState(false);

  const load = useCallback(async () => {
    if (!resourceId) {
      setLoadState('ready');
      return;
    }
    try {
      setData(await getResourceShots(resourceId));
      setLoadState('ready');
    } catch (err) {
      console.error('ShotsTab: failed to load shots', err);
      setLoadState('failed');
    }
  }, [resourceId]);

  useEffect(() => {
    setData(null);
    setLoadState('loading');
    setError(null);
    setWatchedId(null);
    setOptimistic(false);
    void load();
  }, [load]);

  const latestTask = useMemo<UnifiedTask | undefined>(() => {
    if (!resourceId) return undefined;
    const mine = tasks.filter(
      (task) => task.task_type === 'index_shots' && String(task.resource_id) === String(resourceId),
    );
    if (watchedId) {
      const watched = mine.find((task) => task.id === watchedId);
      if (watched) return watched;
    }
    return [...mine].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )[0];
  }, [tasks, resourceId, watchedId]);
  const taskActive = !!latestTask && ACTIVE.has(latestTask.status);

  // Follow a run that was already going when the tab opened, and drop the
  // optimistic state once the dispatched row is visible.
  useEffect(() => {
    if (taskActive && latestTask && watchedId !== latestTask.id) setWatchedId(latestTask.id);
    if (latestTask && latestTask.id === watchedId) setOptimistic(false);
  }, [taskActive, latestTask, watchedId]);

  // The followed task ended: completed → re-read the index; anything else →
  // the typed reason under the button (retry = the same button).
  const watchedStatus = latestTask && latestTask.id === watchedId ? latestTask.status : undefined;
  useEffect(() => {
    if (!watchedId || !watchedStatus || ACTIVE.has(watchedStatus)) return;
    if (watchedStatus === 'completed') {
      void load();
    } else if (latestTask) {
      setError(
        refusalLine(
          latestTask.error_code,
          latestTask.error_msg || tr('detail.shots.errors.failed', 'Shot indexing failed.'),
          tr,
        ),
      );
    }
    setWatchedId(null);
    setOptimistic(false);
  }, [watchedId, watchedStatus, latestTask, load, tr]);

  const start = async (force: boolean) => {
    if (!resourceId) return;
    setBusy(true);
    setError(null);
    try {
      const out = await indexShots(resourceId, { force });
      setWatchedId(out.task_id);
      setOptimistic(true);
    } catch (err) {
      const code = shotErrorCode(err);
      if (code === 'already_indexed') {
        // Someone (or a backfill) got there first: show what exists.
        await load();
        return;
      }
      console.error('ShotsTab: index request failed', err);
      setError(refusalLine(code, err instanceof Error ? err.message : String(err), tr));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!latestTask) return;
    try {
      await cancelTask?.(latestTask.id);
    } catch (err) {
      console.error('ShotsTab: cancel failed', err);
    }
  };

  const hint = (
    <p className="text-xs text-content-3">
      {t('detail.shots.hint', 'Runs as a Task Center task · you can keep browsing')}
    </p>
  );

  if (loadState === 'loading') {
    return (
      <p className="py-12 text-center text-xs text-content-3">{t('detail.shots.loading', 'Loading…')}</p>
    );
  }

  // ── B: indexing ──────────────────────────────────────────────────
  if (taskActive || optimistic) {
    const pct = Math.max(0, Math.min(100, Math.round(latestTask?.progress ?? 0)));
    return (
      <div
        data-testid="shots-indexing"
        className="flex flex-col items-center justify-center py-12 px-4 text-center space-y-3"
      >
        <Loader2 size={28} className="animate-spin text-[var(--accent-text)]" />
        <h3 className="text-sm font-medium text-content">
          {t('detail.shots.indexing', 'Indexing shots…')}
          <span className="ml-2 tabular-nums text-content-2">{pct}%</span>
        </h3>
        <div className="h-1 w-48 overflow-hidden rounded bg-line" aria-hidden="true">
          <div
            data-testid="shots-progress"
            className="h-full bg-[var(--accent-text)]"
            style={{ width: `${pct}%` }}
          />
        </div>
        <p data-testid="shots-subtitle" className="text-xs text-content-2 font-mono">
          {latestTask?.subtitle || t('detail.shots.queued', 'Queued')}
        </p>
        {latestTask && (
          <button
            type="button"
            data-testid="shots-cancel"
            onClick={() => void cancel()}
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-line-strong text-content-2 hover:bg-island"
          >
            {t('detail.shots.cancel', 'Cancel')}
          </button>
        )}
        {hint}
      </div>
    );
  }

  // ── C: indexed ───────────────────────────────────────────────────
  if (data?.indexed && data.index) {
    const { index, shots = [] } = data;
    const totalMs =
      index.duration_ms ??
      (durationSeconds ? Math.round(durationSeconds * 1000) : shots[shots.length - 1]?.end_ms ?? 0);
    const nowMs = currentTimeSeconds != null ? currentTimeSeconds * 1000 : null;
    const isHit = (s: ShotOut) => hitStartMs != null && contains(s, hitStartMs);
    const isCurrent = (s: ShotOut) => nowMs != null && contains(s, nowMs);
    const avgSeconds = index.shot_count > 0 && totalMs > 0 ? totalMs / index.shot_count / 1000 : null;
    const indexedOn = index.indexed_at ? index.indexed_at.slice(0, 10) : '—';
    const seek = (s: ShotOut) => onSeek?.(s.start_ms / 1000);
    return (
      <div data-testid="shots-indexed" className="p-4 sm:p-6 space-y-4 animate-in fade-in duration-300">
        <div className="flex items-center gap-2 text-xs text-content-2">
          <Clapperboard size={14} className="text-content-3" />
          <span data-testid="shots-index-line" className="font-mono truncate">
            {t('detail.shots.indexedLine', 'indexed {{date}} · {{algo}}', {
              date: indexedOn,
              algo: index.algo_version,
            })}
          </span>
          <button
            type="button"
            data-testid="shots-reindex"
            disabled={busy}
            onClick={() => void start(true)}
            className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded text-xs border border-line-strong text-content-2 hover:bg-island disabled:opacity-50"
          >
            <RefreshCw size={11} />
            {t('detail.shots.reindex', 'Re-index')}
          </button>
        </div>
        {!index.covered && (
          <p data-testid="shots-not-covered" className="text-xs text-warn">
            {t(
              'detail.shots.notCovered',
              'No frame vectors in the current embedding space — Re-index to search it visually.',
            )}
          </p>
        )}
        {index.stale && (
          <p data-testid="shots-stale" className="text-xs text-warn">
            {t('detail.shots.stale', 'Cut by an older algorithm — Re-index for the current one.')}
          </p>
        )}
        {error && (
          <p data-testid="shots-error" className="text-xs text-danger">
            {error}
          </p>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Stat
            label={t('detail.shots.stats.shots', 'Shots')}
            value={String(index.shot_count)}
            testId="shots-stat-shots"
          />
          <Stat
            label={t('detail.shots.stats.vectors', 'Vectors · frame')}
            value={String(index.covered ? index.shot_count : 0)}
            testId="shots-stat-vectors"
          />
          <Stat
            label={t('detail.shots.stats.average', 'Average shot')}
            value={avgSeconds != null ? `${avgSeconds.toFixed(1)}s` : '—'}
            testId="shots-stat-average"
          />
          <Stat
            label={t('detail.shots.stats.clusters', 'Clusters')}
            value="—"
            sub={t('detail.shots.stats.clustersSub', 'PR 4')}
            testId="shots-stat-clusters"
          />
        </div>

        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-content-3">
            {t('detail.shots.stripLabel', 'Shot strip · click to seek · outlined = search hit')}
          </p>
          <div data-testid="shots-strip" className="flex h-6 w-full gap-px overflow-hidden rounded">
            {shots.map((s) => {
              const width =
                totalMs > 0 ? Math.max(0.5, ((s.end_ms - s.start_ms) / totalMs) * 100) : 100 / shots.length;
              const hit = isHit(s);
              const current = isCurrent(s);
              return (
                <button
                  key={s.id}
                  type="button"
                  data-testid={`shot-seg-${s.shot_index}`}
                  data-hit={hit ? 'true' : undefined}
                  data-current={current ? 'true' : undefined}
                  title={`${formatClockMs(s.start_ms)} – ${formatClockMs(s.end_ms)}`}
                  onClick={() => seek(s)}
                  style={{ width: `${width}%` }}
                  className={`h-full min-w-0 transition-colors ${
                    current ? 'bg-[var(--accent-text)]' : 'bg-island-2 hover:bg-line-strong'
                  } ${hit ? 'ring-2 ring-inset ring-white' : ''}`}
                />
              );
            })}
          </div>
          <div className="flex justify-between text-[10px] tabular-nums text-content-3">
            <span>0:00</span>
            <span>{formatClockMs(totalMs / 2)}</span>
            <span>{formatClockMs(totalMs)}</span>
          </div>
        </div>

        <ul data-testid="shots-list" className="divide-y divide-line text-xs">
          {shots.map((s) => {
            const hit = isHit(s);
            const current = isCurrent(s);
            return (
              <li key={s.id}>
                <button
                  type="button"
                  data-testid={`shot-row-${s.shot_index}`}
                  data-hit={hit ? 'true' : undefined}
                  onClick={() => seek(s)}
                  className={`flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-island ${
                    hit ? 'bg-accent-soft' : ''
                  } ${current ? 'text-[var(--accent-text)]' : 'text-content'}`}
                >
                  <span className="w-14 shrink-0">
                    {t('detail.shots.shotN', 'Shot {{n}}', { n: s.shot_index + 1 })}
                  </span>
                  <span className="tabular-nums text-content-2">
                    {`${formatClockMs(s.start_ms)} – ${formatClockMs(s.end_ms)}`}
                  </span>
                  <span className="tabular-nums text-content-3">
                    {`${((s.end_ms - s.start_ms) / 1000).toFixed(0)} s`}
                  </span>
                  {hit && (
                    <span className="ml-auto rounded px-1 text-[10px] text-[var(--accent-text)] border border-accent/30">
                      {t('detail.shots.searchHit', 'search hit')}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  // ── A: not indexed ───────────────────────────────────────────────
  const shots = estimateShots(durationSeconds);
  const estimate =
    shots == null || durationSeconds == null
      ? '—'
      : t('detail.shots.estimate', '{{duration}} · ≈ {{shots}} shots · ≈ {{embeddings}} embeddings', {
          duration: formatClockMs(durationSeconds * 1000),
          shots,
          embeddings: shots * EMBEDDINGS_PER_SHOT,
        });
  const canIndex = !!resourceId && !busy;
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4 text-center space-y-3">
      <Clapperboard size={28} className="text-content-3" />
      <h3 className="text-sm font-medium text-content">{t('detail.shots.notIndexed', 'Not Indexed Yet')}</h3>
      <p data-testid="shots-estimate" className="text-xs text-content-2 font-mono">
        {estimate}
      </p>
      {loadState === 'failed' && (
        <p data-testid="shots-load-failed" className="text-xs text-warn">
          {t('detail.shots.loadFailed', 'Could not read the shot index.')}
        </p>
      )}
      <button
        type="button"
        data-testid="shots-index-button"
        disabled={!canIndex}
        title={resourceId ? undefined : t('detail.shots.notInLibrary', 'Not in the library yet')}
        onClick={() => void start(false)}
        className="px-3 py-1.5 rounded-lg text-xs font-medium border border-line-strong text-content hover:bg-island disabled:cursor-not-allowed disabled:text-content-3"
      >
        {error ? t('detail.shots.retry', 'Retry') : t('detail.shots.index', 'Index This Video')}
      </button>
      {error && (
        <p data-testid="shots-error" className="text-xs text-danger max-w-xs">
          {error}
        </p>
      )}
      {hint}
    </div>
  );
};

function Stat({ label, value, sub, testId }: { label: string; value: string; sub?: string; testId: string }) {
  return (
    <div data-testid={testId} className="rounded-lg border border-line bg-island-2 px-3 py-2">
      <div className="text-lg font-semibold tabular-nums text-content">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-content-3">
        {label}
        {sub ? <span className="ml-1 normal-case tracking-normal">· {sub}</span> : null}
      </div>
    </div>
  );
}

export default ShotsTab;
