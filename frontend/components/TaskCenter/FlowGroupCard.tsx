/**
 * FlowGroupCard — flat-list flow card for TaskListView's `groupBy='flow'`
 * mode.
 *
 * What this is NOT:
 *   - Not the rich /tasks/flow/:id detail page (no DAG, no per-task
 *     interaction beyond the existing TaskRow expand).
 *   - Not driven by /api/v1/flows/{id} — it aggregates the live in-page
 *     task list so it stays in sync with Realtime updates without an
 *     extra fetch.
 *
 * What it IS:
 *   - A header that shows aggregate progress for one flow_id group:
 *     done/total counters, a progress bar, elapsed runtime.
 *   - A cascade-cancel button that calls flowService.cancel(flowId)
 *     for any group with at least one non-terminal child (task_flows
 *     trigger handles the per-child cancel; lifecycle bus signals it
 *     to live workers).
 *   - Children render as the existing TaskRow rows (unchanged), so
 *     anything that worked in the flat list still works inside the card.
 *
 * The "__standalone__" group (rows without a flow_id) gets the plain
 * GroupSection treatment in TaskListView; this component only renders
 * for real flows.
 */

import React, { useCallback, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Workflow, X, Loader2 } from 'lucide-react';
import type { TaskGroup } from '../../utils/taskDisplay';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { flowService } from '../../services/flowService';
import { useToast } from '../Toast';

interface FlowGroupCardProps {
  group: TaskGroup;
  /** The flow_id this card represents (already resolved by the caller
   *  via `group.tasks[0].flow_id` — passed in to keep the rendering
   *  pure). */
  flowId: string;
  /** Render prop: how to draw a single child task row. Lets the card
   *  reuse TaskListView's TaskRow without circular imports. */
  renderTask: (t: UnifiedTask) => React.ReactNode;
}

/** ms-precision elapsed string: "Xm Ys" up to 1h, then "Xh Ym". */
function formatElapsed(ms: number): string {
  if (ms < 0) return '0s';
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return sec > 0 ? `${min}m ${sec}s` : `${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'lost']);

export const FlowGroupCard: React.FC<FlowGroupCardProps> = ({ group, flowId, renderTask }) => {
  const [open, setOpen] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const { addToast } = useToast();

  // Aggregate counts derived purely from the in-page task list — no
  // extra API call, stays in sync with Realtime updates automatically.
  const { total, done, failed, cancelled, running, hasActive, percent, elapsed, name } = useMemo(() => {
    const total = group.tasks.length;
    const done = group.tasks.filter((t) => t.status === 'completed').length;
    const failed = group.tasks.filter((t) => t.status === 'failed').length;
    const cancelled = group.tasks.filter((t) => t.status === 'cancelled').length;
    const running = group.tasks.filter((t) => t.status === 'processing').length;
    const hasActive = group.tasks.some((t) => !TERMINAL.has(t.status));
    const percent = total === 0 ? 0 : Math.round(((done + failed + cancelled) / total) * 100);

    // Elapsed = (latest update or now) − earliest created among children.
    let earliest = Infinity;
    let latest = 0;
    let stillRunning = false;
    for (const t of group.tasks) {
      const created = t.created_at ? new Date(t.created_at).getTime() : NaN;
      if (Number.isFinite(created)) earliest = Math.min(earliest, created);
      if (!TERMINAL.has(t.status)) stillRunning = true;
      const end = t.completed_at ?? t.updated_at;
      const endMs = end ? new Date(end).getTime() : NaN;
      if (Number.isFinite(endMs)) latest = Math.max(latest, endMs);
    }
    const endRef = stillRunning ? Date.now() : latest || Date.now();
    const elapsed = Number.isFinite(earliest) ? endRef - earliest : 0;

    // Friendly title: prefer the parse/download root task title; fall
    // back to the short flow id. Avoid "(no title)" placeholders.
    const root = group.tasks.find((t) => t.task_type === 'parse')
      ?? group.tasks.find((t) => t.task_type === 'download')
      ?? group.tasks[0];
    const name = (root?.title && root.title.trim()) || `Flow ${flowId.slice(0, 8)}`;

    return { total, done, failed, cancelled, running, hasActive, percent, elapsed, name };
  }, [group.tasks, flowId]);

  const onCancel = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!hasActive || cancelling) return;
    setCancelling(true);
    try {
      await flowService.cancel(flowId);
      addToast('Flow cancelled — children will stop shortly.', 'success');
    } catch (err) {
      console.error('[FlowGroupCard] cascade cancel failed:', err);
      addToast(`Cancel failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    } finally {
      setCancelling(false);
    }
  }, [flowId, hasActive, cancelling, addToast]);

  // Bar colour: red if any failed, amber if any running, emerald if all done.
  const barColour = failed > 0
    ? 'bg-rose-500'
    : running > 0
      ? 'bg-amber-400'
      : 'bg-emerald-500';

  return (
    <div className="border-b border-ink-800/80 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-2 px-4 py-2 text-left bg-ink-900/40 hover:bg-ink-800/40 sticky top-0"
      >
        {open
          ? <ChevronDown size={11} className="text-ink-500 mt-1 shrink-0" />
          : <ChevronRight size={11} className="text-ink-500 mt-1 shrink-0" />}
        <Workflow size={11} className="text-emerald-400 mt-1 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium text-ink-200 truncate">{name}</span>
            <span
              className="text-[10px] text-ink-500 font-mono shrink-0"
              title={`flow_id ${flowId}`}
            >
              {flowId.slice(0, 8)}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-500">
            <span>{done}/{total} done</span>
            {running > 0 && <span className="text-amber-400">• {running} running</span>}
            {failed > 0 && <span className="text-rose-400">• {failed} failed</span>}
            {cancelled > 0 && <span className="text-ink-500">• {cancelled} cancelled</span>}
            <span>• {formatElapsed(elapsed)}</span>
          </div>
          <div className="mt-1.5 h-1 rounded bg-ink-800/80 overflow-hidden">
            <div
              className={`h-full ${barColour} transition-all duration-300`}
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
        {hasActive && (
          <span
            role="button"
            tabIndex={0}
            onClick={onCancel}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onCancel(e as unknown as React.MouseEvent); }}
            className="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-rose-300 bg-rose-500/10 hover:bg-rose-500/20 ring-1 ring-rose-500/30 cursor-pointer"
            title="Cancel all non-terminal children of this flow"
          >
            {cancelling ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
            Cancel all
          </span>
        )}
      </button>
      {open && (
        <div className="bg-ink-950/30">
          {group.tasks.map((t) => renderTask(t))}
        </div>
      )}
    </div>
  );
};
