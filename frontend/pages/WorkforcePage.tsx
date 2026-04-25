/**
 * Workforce dashboard — read-only board for the M3 persistent runtime.
 *
 * Shows what was previously SQL-only:
 *   - Persistent agents (top cards): state, current task, model, paused reason
 *   - Queue depth per agent: inbox unread/reading, outbox undelivered
 *   - Recent runs per agent: last 5 with cost + tokens + duration
 *   - Recent state transitions: last 20 across all agents (audit feed)
 *
 * Polls `/api/v1/workforce/board` every 5s. Data is small (handful of
 * agents × handful of rows) so polling beats Realtime subscriptions for
 * the first cut — the frontend stays simple and we don't pay for an
 * always-on WebSocket. We can swap to Realtime later if cadence becomes
 * an issue, since the board endpoint already exists.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock,
  Loader2,
  PauseCircle,
  XCircle,
} from 'lucide-react';
import {
  workforceService,
  type WorkforceAgentEntry,
  type WorkforceBoard,
  type WorkforceRecentRun,
  type WorkforceStateHistoryRow,
} from '../services/workforceService';
import { getAgentIcon } from '../components/AILibrary/agentIcons';

const POLL_MS = 5000;

export const WorkforcePage: React.FC = () => {
  const { t } = useTranslation();
  const [board, setBoard] = useState<WorkforceBoard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFetchedAt, setLastFetchedAt] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await workforceService.getBoard();
      setBoard(data);
      setError(null);
      setLastFetchedAt(Date.now());
    } catch (err) {
      console.error('[WorkforcePage] getBoard failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const totalQueued = useMemo(() => {
    if (!board) return 0;
    return board.agents.reduce(
      (acc, a) =>
        acc + a.queue.inbox_unread + a.queue.inbox_reading + a.queue.outbox_undelivered,
      0,
    );
  }, [board]);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">
            {t('workforce.title', 'Workforce')}
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            {t(
              'workforce.subtitle',
              'Persistent agents — current state, queue depth, and recent activity.',
            )}
          </p>
        </div>
        <div className="text-right">
          <div className="text-[11px] text-zinc-500">
            {lastFetchedAt
              ? new Date(lastFetchedAt).toLocaleTimeString()
              : t('workforce.loading', 'Loading…')}
          </div>
          {totalQueued > 0 && (
            <div className="text-[11px] text-amber-400 mt-0.5">
              {t('workforce.queuedItems', '{{count}} queued', { count: totalQueued })}
            </div>
          )}
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-red-900/40 bg-red-950/30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {!board ? (
        <div className="text-sm text-zinc-500">{t('workforce.loading', 'Loading…')}</div>
      ) : board.agents.length === 0 ? (
        <div className="rounded-md border border-zinc-800/60 bg-zinc-900/40 px-4 py-12 text-center text-sm text-zinc-500">
          {t(
            'workforce.empty',
            'No persistent agents. Promote an agent with persistent=true to see it here.',
          )}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {board.agents.map((agent) => (
              <AgentCard key={agent.id} agent={agent} />
            ))}
          </div>

          <RecentHistory rows={board.recent_state_history} />
        </>
      )}
    </div>
  );
};

// ─── card ───────────────────────────────────────────────────────────

const AgentCard: React.FC<{ agent: WorkforceAgentEntry }> = ({ agent }) => {
  const Icon = getAgentIcon(agent.icon);
  const state = agent.worker?.state ?? 'unknown';
  const stateChanged = agent.worker?.state_changed_at;
  const queue = agent.queue;
  const queuedTotal = queue.inbox_unread + queue.inbox_reading + queue.outbox_undelivered;

  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-4 space-y-3">
      <div className="flex items-start gap-3">
        <Icon size={20} className="text-zinc-300 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-zinc-100">{agent.name}</span>
            <StateBadge state={state} />
          </div>
          <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
            {agent.slug}{agent.model ? ` · ${agent.model}` : ''}
          </div>
        </div>
      </div>

      {agent.paused_reason && (
        <div className="flex items-center gap-2 rounded border border-amber-900/30 bg-amber-950/20 px-2 py-1.5 text-[11px] text-amber-400">
          <AlertTriangle size={12} />
          paused — {agent.paused_reason}
        </div>
      )}

      {/* Queue counts */}
      <div className="grid grid-cols-3 gap-2">
        <CountTile label="Inbox" value={queue.inbox_unread + queue.inbox_reading} accent={queuedTotal > 0 ? 'amber' : 'zinc'} />
        <CountTile label="Outbox" value={queue.outbox_undelivered} accent={queue.outbox_undelivered > 0 ? 'amber' : 'zinc'} />
        <CountTile label="State" value={fmtSinceShort(stateChanged)} accent="zinc" />
      </div>

      {/* Recent runs */}
      {agent.recent_runs.length > 0 && (
        <div className="space-y-1">
          <div className="text-[11px] uppercase tracking-wider text-zinc-600 px-1">
            Recent runs
          </div>
          <div className="divide-y divide-zinc-800/40 rounded border border-zinc-800/40 bg-zinc-950/40 overflow-hidden">
            {agent.recent_runs.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const CountTile: React.FC<{ label: string; value: React.ReactNode; accent: 'amber' | 'zinc' }> = ({
  label,
  value,
  accent,
}) => {
  const valueClass = accent === 'amber' ? 'text-amber-300' : 'text-zinc-200';
  return (
    <div className="rounded bg-zinc-950/40 border border-zinc-800/40 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-zinc-600">{label}</div>
      <div className={`text-sm font-medium tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
};

const RunRow: React.FC<{ run: WorkforceRecentRun }> = ({ run }) => {
  const dur = runDurationSeconds(run);
  const cost = run.cost_cents != null ? `${Number(run.cost_cents).toFixed(3)}¢` : '—';
  const total = (run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0);
  return (
    <div className="flex items-center gap-3 px-3 py-1.5 text-[11px]">
      <RunStatusIcon status={run.status} />
      <span className="text-zinc-400 tabular-nums w-14 shrink-0">
        {dur != null ? `${dur.toFixed(1)}s` : '—'}
      </span>
      <span className="text-zinc-500 tabular-nums w-16 shrink-0">{total}t</span>
      <span className="text-zinc-500 tabular-nums w-14 shrink-0">{cost}</span>
      <span className="text-zinc-600 truncate">
        {run.trigger}{' · '}{fmtTime(run.started_at)}
      </span>
    </div>
  );
};

const RunStatusIcon: React.FC<{ status: string }> = ({ status }) => {
  switch (status) {
    case 'completed':
      return <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />;
    case 'running':
      return <Loader2 size={12} className="animate-spin text-indigo-400 shrink-0" />;
    case 'failed':
      return <XCircle size={12} className="text-red-400 shrink-0" />;
    case 'cancelled':
      return <CircleDashed size={12} className="text-zinc-500 shrink-0" />;
    default:
      return <Activity size={12} className="text-zinc-500 shrink-0" />;
  }
};

const StateBadge: React.FC<{ state: string }> = ({ state }) => {
  const cls = stateClass(state);
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${cls}`}>
      <StateIcon state={state} />
      {state}
    </span>
  );
};

const StateIcon: React.FC<{ state: string }> = ({ state }) => {
  switch (state) {
    case 'idle':
      return <CircleDashed size={10} />;
    case 'working':
      return <Loader2 size={10} className="animate-spin" />;
    case 'paused':
      return <PauseCircle size={10} />;
    case 'blocked':
    case 'terminated':
      return <XCircle size={10} />;
    default:
      return <Clock size={10} />;
  }
};

function stateClass(state: string): string {
  switch (state) {
    case 'idle':
      return 'bg-zinc-800/60 text-zinc-300';
    case 'working':
      return 'bg-indigo-500/15 text-indigo-300';
    case 'paused':
      return 'bg-amber-500/15 text-amber-300';
    case 'blocked':
    case 'terminated':
      return 'bg-red-500/15 text-red-300';
    case 'waiting_for_other':
      return 'bg-violet-500/15 text-violet-300';
    default:
      return 'bg-zinc-800/60 text-zinc-500';
  }
}

// ─── recent history ─────────────────────────────────────────────────

const RecentHistory: React.FC<{ rows: WorkforceStateHistoryRow[] }> = ({ rows }) => {
  if (!rows.length) return null;
  return (
    <div className="space-y-2">
      <div className="text-[11px] uppercase tracking-wider text-zinc-600 px-1">
        Recent state transitions
      </div>
      <div className="rounded-md border border-zinc-800/60 bg-zinc-900/40 divide-y divide-zinc-800/40">
        {rows.map((row, i) => (
          <div
            key={`${row.agent_slug}-${row.changed_at}-${i}`}
            className="flex items-center gap-3 px-3 py-1.5 text-[11px]"
          >
            <span className="text-zinc-500 tabular-nums w-16 shrink-0">{fmtTime(row.changed_at)}</span>
            <span className="text-zinc-300 w-20 truncate">{row.agent_slug}</span>
            <span className="text-zinc-600 truncate">
              {row.from_state ?? '·'} → <span className="text-zinc-300">{row.to_state}</span>
              {' · '}
              <span className="text-zinc-500">{row.trigger}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ─── helpers ────────────────────────────────────────────────────────

function fmtTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

function fmtSinceShort(ts: string | null | undefined): string {
  if (!ts) return '—';
  const ms = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function runDurationSeconds(run: WorkforceRecentRun): number | null {
  if (!run.started_at) return null;
  const end = run.ended_at ? new Date(run.ended_at).getTime() : Date.now();
  const start = new Date(run.started_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, (end - start) / 1000);
}

export default WorkforcePage;
