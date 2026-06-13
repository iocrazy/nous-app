import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CheckCircle2, ChevronLeft, ChevronRight, Loader2, RefreshCw,
  XCircle, Ban, HeartCrack, ArrowLeft, X,
} from 'lucide-react';
import type {
  AgentRunDetail, AgentRunListItem, AgentRunListResponse, AgentRunStatus,
} from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { RunTranscript } from './RunTranscript';
import { useToast } from '../Toast';

// Paperclip-style split-pane Runs tab: left = scrollable run list (status
// icon, short id, trigger badge, age, summary snippet, tokens), right =
// selected run detail (status header, token grid, Tasks Touched via the
// mig-282 task linkage, session, summaries, metadata). Replaces the old
// table + modal (AgentEditor RunsSection).

const PAGE_SIZE = 25;
const POLL_INTERVAL_MS = 10_000;

// ─── Shared formatting helpers ─────────────────────────────────────────────

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '0';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function formatCost(centsFractional: number | null | undefined): string {
  if (centsFractional == null) return '—';
  const cents = Number(centsFractional);
  if (!Number.isFinite(cents)) return '—';
  if (cents < 1) return `${cents.toFixed(3)}¢`;
  if (cents < 100) return `${cents.toFixed(2)}¢`;
  return `$${(cents / 100).toFixed(2)}`;
}

function formatClock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(startIso: string, endIso: string | null | undefined): string {
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const secs = Math.round((end - start) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** Friendly label + tint per trigger family (paperclip's invocation-source badge). */
function triggerBadge(trigger: string): { label: string; cls: string } {
  if (trigger.startsWith('visual_analysis')) {
    return { label: 'Vision', cls: 'border-purple-500/40 bg-purple-500/10 text-purple-300' };
  }
  if (trigger.startsWith('summarize')) {
    return { label: 'Summary', cls: 'border-sky-500/40 bg-sky-500/10 text-sky-300' };
  }
  if (trigger === 'chat') {
    return { label: 'Chat', cls: 'border-indigo-500/40 bg-indigo-500/10 text-indigo-300' };
  }
  if (trigger.startsWith('issue')) {
    return { label: 'Issue', cls: 'border-amber-500/40 bg-amber-500/10 text-amber-300' };
  }
  if (trigger.startsWith('script')) {
    return { label: 'Script', cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' };
  }
  return { label: trigger, cls: 'border-ink-700 bg-ink-800 text-ink-300' };
}

const StatusIcon: React.FC<{ status: AgentRunStatus; size?: number }> = ({ status, size = 14 }) => {
  switch (status) {
    case 'running':
      return <Loader2 size={size} className="animate-spin text-emerald-400 shrink-0" />;
    case 'completed':
      return <CheckCircle2 size={size} className="text-emerald-500 shrink-0" />;
    case 'failed':
      return <XCircle size={size} className="text-red-400 shrink-0" />;
    case 'cancelled':
      return <Ban size={size} className="text-amber-400 shrink-0" />;
    case 'heartbeat_lost':
      return <HeartCrack size={size} className="text-orange-400 shrink-0" />;
  }
};

export const RunStatusBadge: React.FC<{ status: AgentRunStatus }> = ({ status }) => {
  const { t } = useTranslation();
  const styles: Record<AgentRunStatus, string> = {
    running: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    completed: 'border-ink-700 bg-ink-800 text-ink-200',
    failed: 'border-red-500/40 bg-red-500/10 text-red-300',
    cancelled: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    heartbeat_lost: 'border-orange-500/40 bg-orange-500/10 text-orange-300',
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${styles[status]}`}>
      {status === 'running' && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
        </span>
      )}
      {t(`aiLibrary.agents.runs.status.${status}`, status)}
    </span>
  );
};

// ─── Left column: run list item ────────────────────────────────────────────

const RunListCard: React.FC<{
  run: AgentRunListItem;
  selected: boolean;
  onSelect: () => void;
}> = ({ run, selected, onSelect }) => {
  const badge = triggerBadge(run.trigger);
  const snippet = (run.output_summary || run.error_code || '').slice(0, 90);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full border-b border-ink-800/70 px-3 py-2.5 text-left transition-colors ${
        selected ? 'bg-ink-800/80' : 'hover:bg-ink-900/70'
      }`}
    >
      <div className="flex items-center gap-2">
        <StatusIcon status={run.status} />
        <span className="font-mono text-xs text-ink-400">{run.id.slice(0, 8)}</span>
        <span className={`rounded border px-1.5 py-px text-[10px] font-medium ${badge.cls}`}>
          {badge.label}
        </span>
        <span className="ml-auto text-[10px] text-ink-500 whitespace-nowrap">
          {relativeAge(run.started_at)}
        </span>
      </div>
      {snippet && (
        <p className="mt-1 line-clamp-2 text-xs leading-snug text-ink-400">{snippet}</p>
      )}
      <div className="mt-1 text-[10px] tabular-nums text-ink-500">
        {formatTokens(run.total_tokens)} tok
        {run.cost_cents != null && <> · {formatCost(run.cost_cents)}</>}
      </div>
    </button>
  );
};

// ─── Right pane: run detail ────────────────────────────────────────────────

const TaskPhaseBadge: React.FC<{ phase?: string | null }> = ({ phase }) => {
  const p = phase || 'unknown';
  const cls =
    p === 'completed'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
      : p === 'failed'
        ? 'border-red-500/40 bg-red-500/10 text-red-300'
        : 'border-ink-700 bg-ink-800 text-ink-300';
  return (
    <span className={`rounded border px-1.5 py-px text-[10px] font-medium ${cls}`}>{p}</span>
  );
};

const Stat: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-[10px] uppercase tracking-wide text-ink-500">{label}</span>
    <span className="text-sm font-semibold tabular-nums text-ink-100">{value}</span>
  </div>
);

const RunDetailPane: React.FC<{
  runId: string;
  onCancel: (runId: string) => void;
  onBack?: () => void;
}> = ({ runId, onCancel, onBack }) => {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await aiLibraryService.getRun(runId);
      setDetail(d);
      setError(null);
    } catch (err) {
      console.error('[RunDetailPane] getRun failed:', err);
      setError(friendlyError(err));
    }
  }, [runId]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    void load();
  }, [load]);

  // While the selected run is live, refresh so tokens/heartbeat advance.
  useEffect(() => {
    if (detail?.status !== 'running') return;
    const id = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [detail?.status, load]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        {error}
      </div>
    );
  }
  if (!detail) {
    return (
      <p className="p-4 text-sm text-ink-500">
        {t('aiLibrary.agents.runs.loading', 'Loading runs...')}
      </p>
    );
  }

  const badge = triggerBadge(detail.trigger);
  const meta = detail.metadata_json || {};

  return (
    <div className="space-y-4">
      {/* Header: status + chips + cancel */}
      <div className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
        <div className="flex flex-wrap items-center gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="md:hidden rounded-md border border-ink-700 bg-ink-800 p-1.5 text-ink-300"
              aria-label={t('common.back', 'Back')}
            >
              <ArrowLeft size={14} />
            </button>
          )}
          <RunStatusBadge status={detail.status} />
          <span className={`rounded border px-1.5 py-px text-[10px] font-medium ${badge.cls}`}>
            {badge.label}
          </span>
          <span className="font-mono text-xs text-ink-500">{detail.trigger}</span>
          {detail.model && (
            <span className="rounded border border-ink-700 bg-ink-800 px-1.5 py-px font-mono text-[10px] text-ink-300">
              {detail.provider ? `${detail.provider}/` : ''}{detail.model}
            </span>
          )}
          <span className="ml-auto" />
          {detail.status === 'running' && !detail.cancel_requested && (
            <button
              type="button"
              onClick={() => onCancel(detail.id)}
              className="rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-500/20"
            >
              {t('aiLibrary.agents.runs.cancel', 'Cancel')}
            </button>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono text-sm text-ink-200">
            {formatClock(detail.started_at)} → {formatClock(detail.ended_at)}
          </span>
          <span className="text-xs text-ink-500">
            {formatTimestamp(detail.started_at)}
          </span>
          <span className="text-xs font-medium text-ink-300">
            {t('aiLibrary.agents.runs.colDuration', 'Duration')}:{' '}
            {formatDuration(detail.started_at, detail.ended_at)}
          </span>
        </div>
        {detail.cancel_requested && detail.status === 'running' && (
          <p className="mt-2 text-xs text-amber-300">
            {t(
              'aiLibrary.agents.runs.cancelPendingNote',
              'Cancel has been requested. The runner will observe it between tool iterations.',
            )}
          </p>
        )}
        {/* Token / cost grid */}
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label={t('aiLibrary.agents.runs.statInput', 'Input')} value={formatTokens(detail.prompt_tokens)} />
          <Stat label={t('aiLibrary.agents.runs.statOutput', 'Output')} value={formatTokens(detail.completion_tokens)} />
          <Stat label={t('aiLibrary.agents.runs.statCached', 'Cached')} value={formatTokens(detail.cached_input_tokens ?? 0)} />
          <Stat label={t('aiLibrary.agents.runs.colCost', 'Cost')} value={formatCost(detail.cost_cents)} />
        </div>
      </div>

      {/* Tasks Touched — mig 282 task ↔ run linkage */}
      {detail.task && (
        <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.tasksTouched', 'Tasks Touched')} (1)
          </h4>
          <div className="flex items-center gap-2 rounded-md border border-ink-800 bg-ink-950/60 px-3 py-2">
            <TaskPhaseBadge phase={detail.task.phase} />
            <span className="truncate text-sm text-ink-200">
              {detail.task.title || detail.task.id}
            </span>
            <span className="ml-auto font-mono text-[10px] text-ink-500">
              {detail.task.task_type || detail.task.id.slice(0, 8)}
            </span>
          </div>
        </section>
      )}

      {/* Transcript (mig 285 event stream) — hidden for pre-285 runs */}
      <RunTranscript runId={detail.id} isRunning={detail.status === 'running'} />

      {/* Session */}
      {detail.session_id && (
        <section className="rounded-lg border border-ink-800 bg-ink-900/60 p-4">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.session', 'Session')}
          </h4>
          <span className="font-mono text-xs text-ink-300 break-all">{detail.session_id}</span>
        </section>
      )}

      {/* Summaries / error / metadata */}
      {detail.input_summary && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.fieldInputSummary', 'Input summary')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-ink-800 bg-ink-950 p-3 text-xs text-ink-200">
            {detail.input_summary}
          </pre>
        </section>
      )}
      {detail.output_summary && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.fieldOutputSummary', 'Output summary')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-ink-800 bg-ink-950 p-3 text-xs text-ink-200">
            {detail.output_summary}
          </pre>
        </section>
      )}
      {detail.error_message && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-red-400">
            {t('aiLibrary.agents.runs.fieldError', 'Error')}
            {detail.error_code ? ` (${detail.error_code})` : ''}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-200">
            {detail.error_message}
          </pre>
        </section>
      )}
      {detail.skill_slugs_used.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.fieldSkills', 'Skills used')}
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {detail.skill_slugs_used.map((s) => (
              <span key={s} className="rounded-full border border-ink-700 bg-ink-800 px-2 py-0.5 text-[10px] text-ink-300">
                {s}
              </span>
            ))}
          </div>
        </section>
      )}
      {Object.keys(meta).length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
            {t('aiLibrary.agents.runs.fieldMetadata', 'Metadata')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-ink-800 bg-ink-950 p-3 font-mono text-xs text-ink-300">
            {JSON.stringify(meta, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
};

// ─── Split-pane container ──────────────────────────────────────────────────

export const AgentRunsSplit: React.FC<{ slug: string }> = ({ slug }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [page, setPage] = useState<AgentRunListResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // Mobile: list ↔ detail toggle (md+ shows both panes).
  const [mobileShowDetail, setMobileShowDetail] = useState(false);
  const offsetRef = useRef(offset);
  offsetRef.current = offset;

  const fetchPage = useCallback(
    async (targetOffset: number, mode: 'initial' | 'poll') => {
      if (mode === 'initial') setLoading(true);
      else setRefreshing(true);
      try {
        const resp = await aiLibraryService.listAgentRuns(slug, PAGE_SIZE, targetOffset);
        setPage(resp);
        setError(null);
        // Auto-select the newest run on first load (desktop behaviour —
        // mobile stays on the list until the user taps a row).
        setSelectedRunId((prev) => prev ?? resp.items[0]?.id ?? null);
      } catch (err) {
        console.error('[AgentRunsSplit] listAgentRuns failed:', err);
        const msg = friendlyError(err);
        setError(msg);
        if (mode === 'initial') {
          addToast(t('aiLibrary.agents.runs.loadRunsError', { error: msg }), 'error');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [slug, addToast, t],
  );

  useEffect(() => {
    void fetchPage(offset, 'initial');
  }, [fetchPage, offset]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void fetchPage(offsetRef.current, 'poll');
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [fetchPage]);

  const handleCancel = async (runId: string): Promise<void> => {
    try {
      await aiLibraryService.cancelRun(runId);
      addToast(t('aiLibrary.agents.runs.cancelRequested', 'Cancel requested'), 'success');
      await fetchPage(offset, 'poll');
    } catch (err) {
      console.error('[AgentRunsSplit] cancelRun failed:', err);
      addToast(
        t('aiLibrary.agents.runs.cancelRunError', { error: friendlyError(err) }),
        'error',
      );
    }
  };

  const total = page?.total ?? 0;
  const items = page?.items ?? [];
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  if (loading && page === null) {
    return (
      <p className="text-sm text-ink-500">
        {t('aiLibrary.agents.runs.loading', 'Loading runs...')}
      </p>
    );
  }
  if (error && page === null) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        {t('aiLibrary.agents.runs.loadError', 'Failed to load runs')}: {error}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-3 py-8 text-center text-sm text-ink-500">
        {t(
          'aiLibrary.agents.runs.emptyBody',
          'This agent has not been invoked yet. Start a chat or task to see activity here.',
        )}
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-3 md:flex-row md:items-start">
      {/* Left: run list */}
      <div
        className={`w-full shrink-0 md:w-72 lg:w-80 ${mobileShowDetail ? 'hidden md:block' : ''}`}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs text-ink-500">
            {t('aiLibrary.agents.runs.paginationLabel', {
              defaultValue: 'Showing {{start}}–{{end}} of {{total}}',
              start: total === 0 ? 0 : offset + 1,
              end: Math.min(offset + PAGE_SIZE, total),
              total,
            })}
          </span>
          <button
            type="button"
            onClick={() => void fetchPage(offset, 'initial')}
            disabled={loading || refreshing}
            className="inline-flex items-center gap-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1 text-[10px] font-medium text-ink-300 hover:bg-ink-700 disabled:opacity-50"
          >
            <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} />
            {t('aiLibrary.agents.runs.refresh', 'Refresh')}
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto rounded-lg border border-ink-800 bg-ink-900/40 custom-scrollbar">
          {items.map((run) => (
            <RunListCard
              key={run.id}
              run={run}
              selected={run.id === selectedRunId}
              onSelect={() => {
                setSelectedRunId(run.id);
                setMobileShowDetail(true);
              }}
            />
          ))}
        </div>
        {(hasPrev || hasNext) && (
          <footer className="mt-2 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={!hasPrev || loading}
              className="inline-flex items-center gap-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1 text-[10px] font-medium text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft size={12} />
              {t('common.previous', 'Previous')}
            </button>
            <button
              type="button"
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={!hasNext || loading}
              className="inline-flex items-center gap-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1 text-[10px] font-medium text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t('common.next', 'Next')}
              <ChevronRight size={12} />
            </button>
          </footer>
        )}
      </div>

      {/* Right: detail pane */}
      <div className={`min-w-0 flex-1 ${mobileShowDetail ? '' : 'hidden md:block'}`}>
        {selectedRunId ? (
          <RunDetailPane
            runId={selectedRunId}
            onCancel={handleCancel}
            onBack={() => setMobileShowDetail(false)}
          />
        ) : (
          <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-ink-800 text-sm text-ink-500">
            <X size={14} className="mr-1.5" />
            {t('aiLibrary.agents.runs.selectPrompt', 'Select a run to view details')}
          </div>
        )}
      </div>
    </section>
  );
};

export default AgentRunsSplit;
