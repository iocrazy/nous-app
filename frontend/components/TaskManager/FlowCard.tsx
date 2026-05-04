/**
 * FlowCard — flow + child task fold-out card.
 *
 * Wraps a `task_flows` row plus its child `task_tracking` rows in a single
 * collapsible UI element. A6 (handoff doc spec): TaskCenter groups by flow
 * and renders one FlowCard per group.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle2,
  XCircle,
  Ban,
  Loader2,
  Workflow,
} from 'lucide-react';
import { flowService, type FlowResponse, type FlowDetailResponse } from '../../services/flowService';
import { useToast } from '../Toast';

interface FlowCardProps {
  flow: FlowResponse;
  /** When true, the card auto-fetches children on mount. */
  expandedByDefault?: boolean;
  /** Notify parent on cascade-cancel so it can refresh. */
  onCancelled?: (flowId: string) => void;
}

const STATE_ICON: Record<string, { icon: React.ReactNode; cls: string; label: string }> = {
  pending:   { icon: <Clock className="w-4 h-4" />,        cls: 'text-gray-500',    label: 'Pending' },
  running:   { icon: <Loader2 className="w-4 h-4 animate-spin" />, cls: 'text-blue-500',  label: 'Running' },
  completed: { icon: <CheckCircle2 className="w-4 h-4" />, cls: 'text-emerald-500', label: 'Completed' },
  failed:    { icon: <XCircle className="w-4 h-4" />,      cls: 'text-rose-500',    label: 'Failed' },
  cancelled: { icon: <Ban className="w-4 h-4" />,          cls: 'text-amber-500',   label: 'Cancelled' },
};

function stateMeta(state: string) {
  return STATE_ICON[state] ?? { icon: <Workflow className="w-4 h-4" />, cls: 'text-gray-500', label: state };
}

export const FlowCard: React.FC<FlowCardProps> = ({ flow, expandedByDefault = false, onCancelled }) => {
  const [expanded, setExpanded] = useState(expandedByDefault);
  const [detail, setDetail] = useState<FlowDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const { addToast } = useToast();

  const meta = stateMeta(flow.state);

  const progressPct = flow.total_tasks > 0
    ? Math.round((flow.completed_tasks / flow.total_tasks) * 100)
    : 0;

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const d = await flowService.get(flow.id);
      setDetail(d);
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Failed to load flow', 'error');
    } finally {
      setLoading(false);
    }
  }, [flow.id, addToast]);

  useEffect(() => {
    if (expanded && !detail && !loading) {
      void fetchDetail();
    }
  }, [expanded, detail, loading, fetchDetail]);

  const onCancel = useCallback(async () => {
    if (!window.confirm(`Cancel flow "${flow.name}"? This will cascade to all child tasks.`)) return;
    setCancelling(true);
    try {
      await flowService.cancel(flow.id);
      addToast('Flow cancellation requested', 'success');
      onCancelled?.(flow.id);
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Cancel failed', 'error');
    } finally {
      setCancelling(false);
    }
  }, [flow.id, flow.name, addToast, onCancelled]);

  const isTerminal = flow.state === 'completed' || flow.state === 'failed' || flow.state === 'cancelled';

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white dark:bg-gray-800">
      <button
        type="button"
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 transition"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
        <span className={meta.cls} title={meta.label}>{meta.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{flow.name}</div>
          <div className="text-xs text-gray-500 dark:text-gray-400">
            {flow.completed_tasks}/{flow.total_tasks} done
            {flow.failed_tasks > 0 && <span className="ml-1 text-rose-500">· {flow.failed_tasks} failed</span>}
            {flow.cancelled_tasks > 0 && <span className="ml-1 text-amber-500">· {flow.cancelled_tasks} cancelled</span>}
          </div>
        </div>
        {flow.total_tasks > 0 && (
          <div className="hidden sm:block w-20 h-1.5 bg-gray-200 dark:bg-gray-700 rounded overflow-hidden">
            <div className="h-full bg-blue-500 transition-all" style={{ width: `${progressPct}%` }} />
          </div>
        )}
      </button>

      {expanded && (
        <div className="border-t border-gray-100 dark:border-gray-700 px-3 py-2 space-y-2">
          {!isTerminal && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onCancel}
                disabled={cancelling}
                className="text-xs px-2 py-1 rounded bg-amber-50 hover:bg-amber-100 text-amber-700 disabled:opacity-50"
              >
                {cancelling ? 'Cancelling…' : 'Cancel flow'}
              </button>
            </div>
          )}
          {loading && <div className="text-xs text-gray-500">Loading tasks…</div>}
          {detail && detail.tasks.length === 0 && (
            <div className="text-xs text-gray-500">No tasks attached yet.</div>
          )}
          {detail && detail.tasks.length > 0 && (
            <ul className="space-y-1">
              {detail.tasks.map((t) => {
                const tMeta = stateMeta(t.status);
                return (
                  <li
                    key={t.dbos_workflow_id}
                    className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 py-1"
                  >
                    <span className={tMeta.cls}>{tMeta.icon}</span>
                    <span className="flex-1 truncate">{t.title}</span>
                    {t.phase && t.phase !== t.status && (
                      <span className="text-[10px] text-gray-400">{t.phase}</span>
                    )}
                    {t.error_msg && (
                      <span className="text-rose-500 truncate max-w-[160px]" title={t.error_msg}>
                        {t.error_msg}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};
