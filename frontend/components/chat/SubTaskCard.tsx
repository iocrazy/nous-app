/**
 * SubTaskCard — TapNow-style sub-task card rendered inline above an
 * assistant message bubble. One card per Skill / Delegate dispatch the
 * LLM made on this turn, in emission order.
 *
 * Compact by default: shows the tool name + a one-line summary
 * (delegate target slug, skill slug, queued/error status). Click to
 * expand and see the raw args + result JSON.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronRight,
  ChevronDown,
  Workflow,
  Wrench,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
} from 'lucide-react';

import type { ChatToolCall } from '../../types';
import { getSupabaseClient } from '../../supabaseClient';
import {
  workforceService,
  TERMINAL_LIFECYCLES,
  type DelegateTaskLookup,
  type TaskLifecycle,
} from '../../services/workforceService';

export interface SubTaskCardProps {
  call: ChatToolCall;
}

export interface ToolCallSummary {
  /** Short title for the collapsed row. */
  label: string;
  /** Status pill text (queued / error / done / loaded). */
  status: string;
  /** True when the result payload contains a string ``error`` field. */
  isError: boolean;
}

/**
 * Compute the one-liner shown in the collapsed sub-task card. Pure
 * function — no React, easy to unit test.
 *
 * Recognised tool shapes:
 *   - Delegate: args = {agent_slug, prompt, ...}, result = {status, ...}
 *   - Skill: args = {skill, file?}, result = {prompt, ...}
 *
 * Any other ``call.name`` falls back to a generic label so future
 * tools render without a code change.
 */
export function summarizeToolCall(call: ChatToolCall): ToolCallSummary {
  const args = call.args ?? {};
  const result = call.result ?? {};

  const errorText =
    typeof result.error === 'string' ? result.error : undefined;
  const isError = Boolean(errorText);

  if (call.name === 'Delegate') {
    const slug = typeof args.agent_slug === 'string' ? args.agent_slug : 'agent';
    const status =
      errorText ||
      (typeof result.status === 'string' ? result.status : 'queued');
    return { label: `→ ${slug}`, status, isError };
  }

  if (call.name === 'Skill') {
    const skillSlug =
      typeof args.skill === 'string' ? args.skill : 'skill';
    const file = typeof args.file === 'string' ? args.file : null;
    const label = file ? `${skillSlug} · ${file}` : skillSlug;
    const status = errorText || (file ? 'loaded' : 'read');
    return { label, status, isError };
  }

  return {
    label: call.name,
    status: errorText || 'done',
    isError,
  };
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface DelegateLiveState {
  /** Authoritative lifecycle when known; null until first fetch resolves. */
  lifecycle: TaskLifecycle | null;
  /** Full task + outbox payload when fetched. */
  data: DelegateTaskLookup | null;
  /** True while the initial GET or a Realtime update is settling. */
  loading: boolean;
}

/**
 * Subscribe to a Delegate dispatch's lifecycle via the workforce
 * lookup endpoint + a Realtime channel on agent_tasks.
 *
 * Lifecycle:
 *   1. Mount → GET /workforce/tasks/by-inbox/:id (initial snapshot).
 *   2. If non-terminal, subscribe to ``agent_tasks`` UPDATE filtered
 *      by ``inbox_message_id=eq.<id>``. INSERT covers the case where
 *      the worker hadn't picked the inbox row up yet on initial fetch.
 *   3. On terminal lifecycle, refetch once to pull the outbox response,
 *      then unsubscribe — avoids leaving a channel open on every old
 *      done card in the chat history.
 *
 * Returning null inboxMessageId disables everything (no-op).
 */
function useDelegateLiveStatus(
  inboxMessageId: string | null,
): DelegateLiveState {
  const [state, setState] = useState<DelegateLiveState>({
    lifecycle: null,
    data: null,
    loading: Boolean(inboxMessageId),
  });
  const refreshRef = useRef<() => Promise<void>>(() => Promise.resolve());

  useEffect(() => {
    if (!inboxMessageId) return;
    let cancelled = false;

    const refresh = async () => {
      try {
        const data = await workforceService.getTaskByInbox(inboxMessageId);
        if (cancelled) return;
        setState({
          lifecycle: data.task?.lifecycle_status ?? null,
          data,
          loading: false,
        });
      } catch (err) {
        if (cancelled) return;
        // Network or 403/404 — fall back to the static result on the
        // chat trace. Surface in console only; no toast (sub-task cards
        // shouldn't pop modals).
        console.warn('[SubTaskCard] getTaskByInbox failed:', err);
        setState((prev) => ({ ...prev, loading: false }));
      }
    };
    refreshRef.current = refresh;
    void refresh();

    const supabase = getSupabaseClient();
    const channel = supabase
      .channel(`delegate-task-${inboxMessageId}`)
      .on(
        'postgres_changes',
        {
          // A4 (migration 200): agent_tasks 合并入 task_tracking。
          // inbox_message_id 仅在 task_kind='agent_task' 行有值，
          // 单 filter 已足够过滤 agent_task 行。
          event: '*',
          schema: 'public',
          table: 'task_tracking',
          filter: `inbox_message_id=eq.${inboxMessageId}`,
        },
        (payload) => {
          const next = (payload.new ?? payload.old) as
            | { phase?: TaskLifecycle; lifecycle_status?: TaskLifecycle }
            | undefined;
          // task_tracking.phase 保留 8-state lifecycle precision
          // (queued / assigned / in_progress / waiting_for_other / blocked /
          //  done / failed / cancelled). agent_tasks.lifecycle_status fallback
          // for transition period.
          const lifecycle = next?.phase ?? next?.lifecycle_status ?? null;
          // Terminal? Refetch once for the outbox payload + final
          // task.result, then leave the channel open until unmount —
          // it's cheap and an idempotent retry handles any flakes.
          setState((prev) => ({ ...prev, lifecycle }));
          if (lifecycle && TERMINAL_LIFECYCLES.has(lifecycle)) {
            void refreshRef.current();
          }
        },
      )
      .subscribe();

    return () => {
      cancelled = true;
      void supabase.removeChannel(channel);
    };
  }, [inboxMessageId]);

  return state;
}

function workforceLinkFor(slug: string): string {
  // The workforce dashboard auto-opens the agent drawer when ``agent``
  // query param matches a slug. New tab so the chat session isn't lost.
  return `/workforce?agent=${encodeURIComponent(slug)}`;
}

export function SubTaskCard({ call }: SubTaskCardProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const summary = summarizeToolCall(call);

  // Delegate cards subscribe to the sub-agent's lifecycle so the user
  // can watch queued → in_progress → done in real time. Skill cards
  // are synchronous — no lifecycle to follow.
  const inboxMessageId = useMemo<string | null>(() => {
    if (call.name !== 'Delegate') return null;
    const id = call.result?.inbox_message_id;
    return typeof id === 'string' ? id : null;
  }, [call]);

  const live = useDelegateLiveStatus(inboxMessageId);

  // Live lifecycle (when known) wins over the static "queued" the
  // Delegate tool returned; this is the whole point of the realtime
  // subscription — making the card feel alive while the worker runs.
  const effectiveStatus = live.lifecycle ?? summary.status;
  const isInFlight =
    live.lifecycle != null && !TERMINAL_LIFECYCLES.has(live.lifecycle);
  const isError =
    summary.isError || live.lifecycle === 'failed';

  const targetSlug =
    call.name === 'Delegate' && typeof call.args?.agent_slug === 'string'
      ? (call.args.agent_slug as string)
      : null;

  const Icon = call.name === 'Delegate' ? Workflow : Wrench;
  let StatusIcon: typeof CheckCircle2 = CheckCircle2;
  if (isError) StatusIcon = AlertTriangle;
  else if (isInFlight) StatusIcon = Loader2;

  const accent = isError
    ? 'text-red-400 bg-red-500/10 border-red-500/30'
    : isInFlight
      ? 'text-indigo-300 bg-indigo-500/15 border-indigo-500/40'
      : call.name === 'Delegate'
        ? 'text-indigo-300 bg-indigo-500/10 border-indigo-500/30'
        : 'text-amber-300 bg-amber-500/10 border-amber-500/30';

  return (
    <div className={`rounded-lg border ${accent} text-xs mb-1.5`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left hover:bg-white/5 rounded-lg"
      >
        {expanded ? (
          <ChevronDown size={12} className="flex-shrink-0 opacity-70" />
        ) : (
          <ChevronRight size={12} className="flex-shrink-0 opacity-70" />
        )}
        <Icon size={12} className="flex-shrink-0" />
        <span className="font-medium flex-1 truncate" title={summary.label}>
          {summary.label}
        </span>
        <StatusIcon
          size={11}
          className={`flex-shrink-0 opacity-80 ${isInFlight ? 'animate-spin' : ''}`}
        />
        <span
          className="opacity-80 truncate max-w-[40%]"
          title={effectiveStatus}
        >
          {effectiveStatus}
        </span>
      </button>

      {expanded && (
        <div className="px-2 pb-2 pt-1 space-y-2 border-t border-white/10">
          {targetSlug && (
            <div className="flex items-center gap-2 text-[10px]">
              <a
                href={workforceLinkFor(targetSlug)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/10 text-ink-200 hover:bg-white/20"
              >
                <ExternalLink size={10} />
                View {targetSlug} in Workforce
              </a>
              {live.loading && (
                <span className="opacity-60 inline-flex items-center gap-1">
                  <Loader2 size={10} className="animate-spin" />
                  syncing…
                </span>
              )}
            </div>
          )}
          <div>
            <div className="text-[10px] uppercase tracking-wide opacity-60 mb-0.5">
              args
            </div>
            <pre className="text-[10px] font-mono bg-black/30 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-words">
              {safeStringify(call.args)}
            </pre>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide opacity-60 mb-0.5">
              {live.data?.task ? 'task' : 'result'}
            </div>
            <pre className="text-[10px] font-mono bg-black/30 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
              {safeStringify(live.data?.task ?? call.result)}
            </pre>
          </div>
          {live.data?.outbox_response && (
            <div>
              <div className="text-[10px] uppercase tracking-wide opacity-60 mb-0.5">
                response
              </div>
              <pre className="text-[10px] font-mono bg-black/30 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {safeStringify(live.data.outbox_response.payload)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export interface SubTaskListProps {
  calls: ChatToolCall[];
}

/**
 * Renders the sub-task list above an assistant bubble. Returns null
 * when empty so callers can drop it in unconditionally.
 */
export function SubTaskList({ calls }: SubTaskListProps): React.ReactElement | null {
  if (!calls || calls.length === 0) return null;
  return (
    <div className="px-3 pt-2">
      {calls.map((call, idx) => (
        <SubTaskCard
          key={`${call.iteration}-${call.name}-${idx}`}
          call={call}
        />
      ))}
    </div>
  );
}
