/**
 * Side drawer that shows the deep-dive view for one persistent agent.
 *
 * Tabs: Inbox / Outbox / Runs. Each list shows the most recent 20
 * rows with payload preview, timestamps, status badges. Opens via
 * AgentCard click in WorkforcePage; closes via X / Esc / backdrop.
 *
 * Polling cadence: refresh on open and on Realtime burst (debounced
 * 500ms — slower than the board's 200ms because the drawer is a more
 * expensive query). No auto-poll while open; user clicks Refresh or
 * receives a Realtime nudge.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  CircleDashed,
  Loader2,
  RefreshCw,
  X,
  XCircle,
  ListTodo,
  HelpCircle,
  ShieldAlert,
  Bell,
  Slash,
  Search,
  Inbox as InboxIcon,
} from 'lucide-react';
import {
  workforceService,
  type WorkforceAgentDetail,
  type WorkforceDetailRun,
  type WorkforceInboxRow,
  type WorkforceOutboxRow,
} from '../../services/workforceService';
import { getSupabaseClient } from '../../supabaseClient';

interface AgentDetailDrawerProps {
  slug: string;
  onClose: () => void;
}

type Tab = 'inbox' | 'outbox' | 'runs';

const REALTIME_DEBOUNCE_MS = 500;

export const AgentDetailDrawer: React.FC<AgentDetailDrawerProps> = ({
  slug,
  onClose,
}) => {
  const [detail, setDetail] = useState<WorkforceAgentDetail | null>(null);
  const [tab, setTab] = useState<Tab>('runs');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await workforceService.getAgentDetail(slug);
      setDetail(data);
      setError(null);
    } catch (err) {
      console.error('[AgentDetailDrawer] getAgentDetail failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [slug]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Esc closes drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Realtime: debounced refresh on any workforce table change.
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    const supabase = getSupabaseClient();
    const channel = supabase.channel(`workforce-detail-${slug}`);
    for (const table of [
      'agent_workers',
      'agent_inbox',
      'agent_outbox',
      'agent_state_history',
      'agent_tasks',
      'agent_runs',
    ] as const) {
      channel.on(
        // @ts-expect-error — see WorkforcePage for the same supabase-js
        // type/runtime divergence on '*' postgres_changes events.
        'postgres_changes',
        { event: '*', schema: 'public', table },
        () => {
          if (debounceRef.current != null) {
            window.clearTimeout(debounceRef.current);
          }
          debounceRef.current = window.setTimeout(() => {
            debounceRef.current = null;
            void refresh();
          }, REALTIME_DEBOUNCE_MS);
        },
      );
    }
    void channel.subscribe();
    return () => {
      void supabase.removeChannel(channel);
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [slug, refresh]);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Panel */}
      <aside className="fixed top-0 right-0 z-50 h-full w-full max-w-2xl bg-zinc-950 border-l border-zinc-800 flex flex-col shadow-2xl">
        <header className="flex items-center justify-between px-5 py-4 border-b border-zinc-800/60">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100 truncate">
              {detail?.agent.name ?? slug}
            </div>
            <div className="text-[11px] text-zinc-500 truncate">
              {detail?.agent.slug ?? slug}
              {detail?.agent.model ? ` · ${detail.agent.model}` : ''}
              {detail?.agent.paused_reason
                ? ` · paused: ${detail.agent.paused_reason}`
                : ''}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              aria-label="Refresh"
              title="Refresh"
            >
              <RefreshCw size={14} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              aria-label="Close"
              title="Close (Esc)"
            >
              <X size={14} />
            </button>
          </div>
        </header>

        {/* Tabs */}
        <nav className="flex items-center gap-1 px-3 pt-2 border-b border-zinc-800/40">
          {(
            [
              ['runs', `Runs${detail ? ` (${detail.runs.length})` : ''}`],
              ['inbox', `Inbox${detail ? ` (${detail.inbox.length})` : ''}`],
              ['outbox', `Outbox${detail ? ` (${detail.outbox.length})` : ''}`],
            ] as const
          ).map(([id, label]) => {
            const active = tab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`px-3 py-2 text-[12px] border-b-2 transition-colors ${
                  active
                    ? 'border-indigo-400 text-zinc-100'
                    : 'border-transparent text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {label}
              </button>
            );
          })}
        </nav>

        <div className="flex-1 overflow-y-auto">
          {error && (
            <div className="m-4 rounded-md border border-red-900/40 bg-red-950/30 px-4 py-3 text-[12px] text-red-300">
              {error}
            </div>
          )}
          {!detail ? (
            <div className="p-6 text-sm text-zinc-500">Loading…</div>
          ) : (
            <>
              {tab === 'runs' && <RunsList runs={detail.runs} />}
              {tab === 'inbox' && <InboxList rows={detail.inbox} />}
              {tab === 'outbox' && <OutboxList rows={detail.outbox} />}
            </>
          )}
        </div>
      </aside>
    </>
  );
};

// ─── lists ──────────────────────────────────────────────────────────

const RunsList: React.FC<{ runs: WorkforceDetailRun[] }> = ({ runs }) => {
  if (!runs.length) {
    return <Empty label="No runs yet." />;
  }
  return (
    <ul className="divide-y divide-zinc-800/40">
      {runs.map((run) => (
        <li key={run.id} className="px-5 py-3 hover:bg-zinc-900/40">
          <div className="flex items-start gap-3">
            <RunStatusIcon status={run.status} />
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                <span className="text-zinc-300">{run.trigger}</span>
                <span>·</span>
                <span>{fmtTime(run.started_at)}</span>
                <span>·</span>
                <span>{fmtDuration(run.started_at, run.ended_at)}</span>
                <span>·</span>
                <span>
                  {(run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)}t
                </span>
                <span>·</span>
                <span>
                  {run.cost_cents != null
                    ? `${Number(run.cost_cents).toFixed(3)}¢`
                    : '—'}
                </span>
                {run.model && (
                  <>
                    <span>·</span>
                    <span className="truncate">{run.model}</span>
                  </>
                )}
              </div>
              {run.input_summary && (
                <div className="text-[12px] text-zinc-300 line-clamp-2">
                  <span className="text-zinc-500">in: </span>
                  {run.input_summary}
                </div>
              )}
              {run.output_summary && (
                <div className="text-[12px] text-zinc-400 line-clamp-3">
                  <span className="text-zinc-500">out: </span>
                  {run.output_summary}
                </div>
              )}
              {run.error_message && (
                <div className="text-[12px] text-red-400">
                  <span className="text-zinc-500">error: </span>
                  {run.error_code} — {run.error_message}
                </div>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
};

// G4: per-message-type styling. Each type gets its own icon + accent
// so an operator scanning the list can spot e.g. an approval_request
// among 50 routine task messages.
const MESSAGE_TYPE_META: Record<
  string,
  { icon: React.ReactNode; label: string; accent: string }
> = {
  task: {
    icon: <ListTodo className="w-3 h-3" />,
    label: 'Task',
    accent: 'bg-blue-600/20 text-blue-300 border-blue-700/40',
  },
  question: {
    icon: <HelpCircle className="w-3 h-3" />,
    label: 'Question',
    accent: 'bg-cyan-600/20 text-cyan-300 border-cyan-700/40',
  },
  approval_request: {
    icon: <ShieldAlert className="w-3 h-3" />,
    label: 'Approval',
    accent: 'bg-amber-600/20 text-amber-300 border-amber-700/40',
  },
  notification: {
    icon: <Bell className="w-3 h-3" />,
    label: 'Notification',
    accent: 'bg-zinc-700/40 text-zinc-300 border-zinc-700',
  },
  cancel: {
    icon: <Slash className="w-3 h-3" />,
    label: 'Cancel',
    accent: 'bg-red-600/20 text-red-300 border-red-700/40',
  },
  status_query: {
    icon: <Search className="w-3 h-3" />,
    label: 'Status query',
    accent: 'bg-purple-600/20 text-purple-300 border-purple-700/40',
  },
};

function _typeMeta(t: string) {
  return (
    MESSAGE_TYPE_META[t] || {
      icon: <InboxIcon className="w-3 h-3" />,
      label: t,
      accent: 'bg-zinc-700/40 text-zinc-300 border-zinc-700',
    }
  );
}

const InboxList: React.FC<{ rows: WorkforceInboxRow[] }> = ({ rows }) => {
  // G4: filter chips above the list so operator can isolate a single type
  const allTypes = React.useMemo(
    () => Array.from(new Set(rows.map((r) => r.message_type))).sort(),
    [rows],
  );
  const [filter, setFilter] = useState<string | null>(null);
  const filtered = filter ? rows.filter((r) => r.message_type === filter) : rows;

  if (!rows.length) {
    return <Empty label="No inbox messages." />;
  }
  return (
    <div>
      {allTypes.length > 1 && (
        <div className="flex items-center gap-1 flex-wrap px-5 py-2 border-b border-zinc-800/40 bg-zinc-900/30">
          <span className="text-[10px] text-zinc-600 mr-1">Filter:</span>
          <button
            type="button"
            onClick={() => setFilter(null)}
            className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
              filter === null
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-500 hover:bg-zinc-800'
            }`}
          >
            All ({rows.length})
          </button>
          {allTypes.map((t) => {
            const meta = _typeMeta(t);
            const count = rows.filter((r) => r.message_type === t).length;
            return (
              <button
                key={t}
                type="button"
                onClick={() => setFilter(t)}
                className={`px-1.5 py-0.5 text-[10px] rounded border flex items-center gap-1 transition-colors ${
                  filter === t ? meta.accent : 'text-zinc-500 border-transparent hover:bg-zinc-800'
                }`}
              >
                {meta.icon}
                {meta.label} ({count})
              </button>
            );
          })}
        </div>
      )}
      <ul className="divide-y divide-zinc-800/40">
        {filtered.map((row) => {
          const meta = _typeMeta(row.message_type);
          return (
            <li key={row.id} className="px-5 py-3 hover:bg-zinc-900/40">
              <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                <StatusBadge value={row.status} />
                <span
                  className={`flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] ${meta.accent}`}
                  title={row.message_type}
                >
                  {meta.icon}
                  {meta.label}
                </span>
                <span>·</span>
                <span>from {row.sender_kind}</span>
                <span>·</span>
                <span>p{row.priority ?? 5}</span>
                <span>·</span>
                <span>{fmtTime(row.created_at)}</span>
                {row.processed_at && (
                  <>
                    <span>·</span>
                    <span>processed {fmtTime(row.processed_at)}</span>
                  </>
                )}
              </div>
              {row.payload && <PayloadPreview payload={row.payload} max={240} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

const OutboxList: React.FC<{ rows: WorkforceOutboxRow[] }> = ({ rows }) => {
  if (!rows.length) {
    return <Empty label="No outbox messages." />;
  }
  return (
    <ul className="divide-y divide-zinc-800/40">
      {rows.map((row) => (
        <li key={row.id} className="px-5 py-3 hover:bg-zinc-900/40">
          {(() => { const meta = _typeMeta(row.message_type); return (
          <div className="flex items-center gap-2 text-[11px] text-zinc-500">
            <StatusBadge value={row.delivered ? 'delivered' : 'pending'} />
            <span
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] ${meta.accent}`}
              title={row.message_type}
            >
              {meta.icon}
              {meta.label}
            </span>
            <span>·</span>
            <span>to {row.recipient_kind}</span>
            <span>·</span>
            <span>{fmtTime(row.created_at)}</span>
            {row.delivered_at && (
              <>
                <span>·</span>
                <span>delivered {fmtTime(row.delivered_at)}</span>
              </>
            )}
          </div>
          ); })()}
          {row.payload && (
            <PayloadPreview payload={row.payload} max={240} />
          )}
        </li>
      ))}
    </ul>
  );
};

// ─── tiny helpers ───────────────────────────────────────────────────

const Empty: React.FC<{ label: string }> = ({ label }) => (
  <div className="p-6 text-sm text-zinc-500 text-center">{label}</div>
);

const RunStatusIcon: React.FC<{ status: string }> = ({ status }) => {
  switch (status) {
    case 'completed':
      return <CheckCircle2 size={14} className="text-emerald-400 shrink-0 mt-0.5" />;
    case 'running':
      return <Loader2 size={14} className="animate-spin text-indigo-400 shrink-0 mt-0.5" />;
    case 'failed':
      return <XCircle size={14} className="text-red-400 shrink-0 mt-0.5" />;
    case 'cancelled':
      return <CircleDashed size={14} className="text-zinc-500 shrink-0 mt-0.5" />;
    default:
      return <CircleDashed size={14} className="text-zinc-500 shrink-0 mt-0.5" />;
  }
};

const StatusBadge: React.FC<{ value: string }> = ({ value }) => {
  const cls = (() => {
    switch (value) {
      case 'unread':
        return 'bg-indigo-500/15 text-indigo-300';
      case 'reading':
        return 'bg-amber-500/15 text-amber-300';
      case 'processed':
      case 'delivered':
        return 'bg-emerald-500/15 text-emerald-300';
      case 'dismissed':
      case 'expired':
        return 'bg-zinc-800/60 text-zinc-400';
      case 'pending':
        return 'bg-amber-500/15 text-amber-300';
      default:
        return 'bg-zinc-800/60 text-zinc-400';
    }
  })();
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${cls}`}>
      {value}
    </span>
  );
};

const PayloadPreview: React.FC<{
  payload: Record<string, unknown>;
  max: number;
}> = ({ payload, max }) => {
  const text = useMemo(() => {
    const title = payload.title ?? null;
    const prompt = payload.prompt ?? payload.content ?? null;
    if (typeof title === 'string' || typeof prompt === 'string') {
      return [title, prompt].filter(Boolean).join(' — ');
    }
    try {
      return JSON.stringify(payload);
    } catch {
      return '<unserialisable>';
    }
  }, [payload]);
  const trimmed = text.length > max ? text.slice(0, max) + '…' : text;
  return (
    <div className="text-[12px] text-zinc-400 mt-1 line-clamp-3 break-words">
      {trimmed}
    </div>
  );
};

function fmtTime(ts: string | null): string {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function fmtDuration(start: string | null, end: string | null): string {
  if (!start) return '—';
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return '—';
  const seconds = Math.max(0, (endMs - startMs) / 1000);
  return `${seconds.toFixed(1)}s`;
}

export default AgentDetailDrawer;
