import React, { useCallback, useMemo, useState } from 'react';
import { Ban, CheckCircle2, Loader2, X, XCircle } from 'lucide-react';
import {
  formatSpeed,
  taskTypeLabel,
  type UnifiedTask,
} from '../../contexts/TaskManagerContext';
import { TaskTypeIcon } from './TaskTypeIcon';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { getResourceCoverUrl } from '../../services/resourceService';
import { flowService } from '../../services/flowService';
import { failureLabel } from '../../utils/taskFailure';
import { type FlowItem, type PanelItem, flowDisplayTitle } from './flowGrouping';
import { ActiveTaskCard } from './ActiveTaskCard';
import { TaskCenterRow } from './TaskCenterRow';

// Above this many steps, render a progress bar instead of per-step circles.
// Normal chains are ~3-6 steps; a batch playlist can be 200+ (one circle each
// is unreadable and grows unbounded).
const COMPACT_RAIL_THRESHOLD = 12;

/**
 * Compact flow card for the floating Task Center panel: one user submission
 * (parse → download → thumbnail / extract_audio / transcode / ai_*) renders
 * as ONE card with a step-circle rail instead of N sibling rows.
 *
 *   - one circle per pipeline step: ✓ done · progress ring while running ·
 *     hollow while queued · ✕ on failure
 *   - the in-flight step auto-expands below the rail (rich live card with
 *     progress + speed); clicking any circle pins that step open instead
 *   - circles appear dynamically as chained workflows dispatch (AI steps are
 *     tag-conditional — no phantom placeholders)
 */

interface FlowStepCardProps {
  flow: FlowItem;
  /** Shared 1s clock from the panel for live elapsed time. */
  now: number;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  onOpenResource: (resourceId: string) => void;
  onOpenDetail: (task: UnifiedTask) => void;
}

/** Tiny SVG progress ring for the in-flight step circle. */
const StepRing: React.FC<{ progress: number }> = ({ progress }) => {
  const r = 6;
  const c = 2 * Math.PI * r;
  const clamped = Math.min(Math.max(progress, 0), 100);
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" className="-rotate-90 text-amber-400">
      <circle cx={8} cy={8} r={r} fill="none" stroke="currentColor" strokeOpacity={0.25} strokeWidth={2.5} />
      <circle
        cx={8}
        cy={8}
        r={r}
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - clamped / 100)}
        className="transition-all duration-500"
      />
    </svg>
  );
};

const StepCircle: React.FC<{ step: UnifiedTask; active: boolean }> = ({ step, active }) => {
  switch (step.status) {
    case 'completed':
      return <CheckCircle2 size={16} className="text-emerald-400" />;
    case 'failed':
      return <XCircle size={16} className="text-rose-400" />;
    case 'cancelled':
      return <Ban size={16} className="text-ink-500" />;
    case 'processing':
      return <StepRing progress={step.progress || 0} />;
    case 'pending':
    default:
      return (
        <span
          className={`block w-[14px] h-[14px] rounded-full border-2 ${
            active ? 'border-ink-400' : 'border-ink-600'
          }`}
        />
      );
  }
};

export const FlowStepCard: React.FC<FlowStepCardProps> = ({
  flow,
  now,
  onCancel,
  onRetry,
  onOpenResource,
  onOpenDetail,
}) => {
  const { mediaToken } = useAuth();
  const { addToast } = useToast();
  const [coverFailed, setCoverFailed] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // null = follow the in-flight step; task id = user pinned a step open;
  // 'collapsed' = user explicitly closed the expanded area.
  const [pinned, setPinned] = useState<string | 'collapsed' | null>(null);

  const title = useMemo(() => flowDisplayTitle(flow), [flow]);

  const expandedId =
    pinned === 'collapsed'
      ? null
      : pinned ?? (flow.hasActive ? flow.current?.id ?? null : null);
  const expandedStep = expandedId
    ? flow.steps.find((s) => s.id === expandedId) ?? null
    : null;

  const toggleStep = useCallback(
    (id: string) => setPinned((_) => (expandedId === id ? 'collapsed' : id)),
    [expandedId],
  );

  // Cover: any step that produced a resource can supply the thumbnail.
  const coverRid = flow.steps.find((s) => s.resource_id)?.resource_id;

  // Status line under the rail: the in-flight step's live numbers, or the
  // first failure, or a quiet "all done".
  const statusLine = useMemo(() => {
    const cur = flow.current;
    if (cur) {
      const label = taskTypeLabel(cur.task_type);
      if (cur.status === 'processing') {
        const bits = [label, `${Math.round(cur.progress || 0)}%`];
        if (cur.speed && cur.speed > 0) bits.push(formatSpeed(cur.speed));
        return { text: bits.join(' · '), tone: 'text-warn' };
      }
      return { text: `${label} · queued`, tone: 'text-ink-500' };
    }
    const failed = flow.steps.find((s) => s.status === 'failed');
    if (failed) {
      const reason = failureLabel(failed.error_msg) || 'failed';
      return {
        text: `${taskTypeLabel(failed.task_type)} · ${reason}`,
        tone: 'text-rose-400',
      };
    }
    return { text: `${flow.doneCount}/${flow.steps.length} steps`, tone: 'text-ink-500' };
  }, [flow]);

  const onCancelFlow = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!flow.hasActive || cancelling) return;
      setCancelling(true);
      try {
        await flowService.cancel(flow.flowId);
        addToast('Flow cancelled — steps will stop shortly.', 'success');
      } catch (err) {
        console.error('[FlowStepCard] cascade cancel failed:', err);
        addToast(`Cancel failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
      } finally {
        setCancelling(false);
      }
    },
    [flow.flowId, flow.hasActive, cancelling, addToast],
  );

  const aggregateBadge = flow.hasActive ? (
    <Loader2 size={14} className="text-amber-400 animate-spin shrink-0" />
  ) : flow.failedCount > 0 ? (
    <XCircle size={14} className="text-rose-400 shrink-0" />
  ) : (
    <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
  );

  return (
    <div className="border-b border-ink-800/50 last:border-b-0">
      <div className="px-3 py-2.5">
        <div className="flex items-start gap-2.5">
          {/* Cover thumbnail (falls back to the root step's type icon) */}
          {coverRid && !coverFailed ? (
            <img
              src={getResourceCoverUrl(String(coverRid), mediaToken ?? undefined)}
              alt=""
              onError={() => setCoverFailed(true)}
              className="w-9 h-9 rounded-lg object-cover shrink-0 bg-ink-800"
            />
          ) : (
            <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-sm bg-purple-500/15 text-purple-300">
              <TaskTypeIcon type={flow.steps[0]?.task_type ?? 'download'} size={16} />
            </div>
          )}

          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-ink-200 truncate">{title}</span>
              <div className="flex items-center gap-1.5 shrink-0">
                {flow.hasActive && (
                  <button
                    onClick={onCancelFlow}
                    className="p-0.5 rounded text-ink-500 hover:text-rose-400 transition-colors"
                    title="Cancel all steps of this task"
                  >
                    {cancelling ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  </button>
                )}
                {aggregateBadge}
              </div>
            </div>

            {/* Step display: a per-step circle rail for normal chains, but a
                compact progress bar for large batches (e.g. a 226-track
                playlist) — 200+ circles in a row is unreadable and unbounded. */}
            {flow.steps.length > COMPACT_RAIL_THRESHOLD ? (
              (() => {
                const total = flow.steps.length;
                const done = flow.steps.filter((s) => s.status === 'completed').length;
                const failed = flow.steps.filter(
                  (s) => s.status === 'failed' || s.status === 'cancelled',
                ).length;
                const pending = total - done - failed;
                const pct = total ? Math.round(((done + failed) / total) * 100) : 0;
                return (
                  <div className="mt-1.5">
                    <div className="h-1.5 rounded-full bg-ink-800 overflow-hidden flex">
                      <div
                        className="h-full bg-emerald-500 transition-all"
                        style={{ width: `${total ? (done / total) * 100 : 0}%` }}
                      />
                      <div
                        className="h-full bg-red-500/80 transition-all"
                        style={{ width: `${total ? (failed / total) * 100 : 0}%` }}
                      />
                    </div>
                    <div className="mt-1 text-[10px] text-ink-400">
                      {done}/{total} done
                      {failed > 0 ? ` · ${failed} failed` : ''}
                      {pending > 0 ? ` · ${pending} pending` : ''} · {pct}%
                    </div>
                  </div>
                );
              })()
            ) : (
              <div className="mt-1.5 flex items-center">
                {flow.steps.map((step, i) => (
                  <React.Fragment key={step.id}>
                    {i > 0 && (
                      <span
                        className={`h-px w-3 ${
                          step.status === 'completed' || flow.steps[i - 1].status === 'completed'
                            ? 'bg-ink-600'
                            : 'bg-ink-800'
                        }`}
                      />
                    )}
                    <button
                      onClick={() => toggleStep(step.id)}
                      data-testid="flow-step"
                      data-step-status={step.status}
                      title={`${taskTypeLabel(step.task_type)} · ${step.status}${
                        (flow.attemptCounts[step.id] ?? 1) > 1
                          ? ` · ${flow.attemptCounts[step.id]} attempts`
                          : ''
                      }`}
                      className={`flex items-center p-0.5 rounded-full transition-transform hover:scale-125 ${
                        expandedId === step.id ? 'ring-1 ring-ink-500/60' : ''
                      }`}
                    >
                      <StepCircle step={step} active={step.id === flow.current?.id} />
                      {/* Retries collapse into their step; the badge keeps the
                          earlier attempts visible instead of erasing them. */}
                      {(flow.attemptCounts[step.id] ?? 1) > 1 && (
                        <span
                          data-testid="flow-step-attempts"
                          className="ml-0.5 text-[9px] leading-none text-ink-400 tabular-nums"
                        >
                          ×{flow.attemptCounts[step.id]}
                        </span>
                      )}
                    </button>
                  </React.Fragment>
                ))}
              </div>
            )}

            <div className={`mt-1 text-[10px] truncate ${statusLine.tone}`}>{statusLine.text}</div>
          </div>
        </div>
      </div>

      {/* Expanded step: rich live card while running, plain row otherwise.
          Indented behind a vertical guide line so the sub-step visually
          belongs to the flow card above it. */}
      {expandedStep && (
        <div className="ml-7 mr-2 mb-2 border-l-2 border-ink-700/70 bg-ink-900/30 rounded-r-lg">
          {expandedStep.status === 'processing' ? (
            <ActiveTaskCard task={expandedStep} now={now} onCancel={onCancel} />
          ) : (
            <TaskCenterRow
              task={expandedStep}
              onCancel={onCancel}
              onRetry={onRetry}
              onOpenResource={onOpenResource}
              onOpenDetail={onOpenDetail}
            />
          )}
        </div>
      )}
    </div>
  );
};

interface FlowTaskListProps {
  items: PanelItem[];
  now: number;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  onOpenResource: (resourceId: string) => void;
  onOpenDetail: (task: UnifiedTask) => void;
}

/**
 * Shared renderer for the floating Task Center panel + the mobile tasks
 * page: flows render as FlowStepCard, standalone tasks keep their old
 * shapes (live card while processing, plain row otherwise).
 */
export const FlowTaskList: React.FC<FlowTaskListProps> = ({
  items,
  now,
  onCancel,
  onRetry,
  onOpenResource,
  onOpenDetail,
}) => (
  <>
    {items.map((item) =>
      item.kind === 'flow' ? (
        <FlowStepCard
          key={`flow-${item.flowId}`}
          flow={item}
          now={now}
          onCancel={onCancel}
          onRetry={onRetry}
          onOpenResource={onOpenResource}
          onOpenDetail={onOpenDetail}
        />
      ) : item.task.status === 'processing' ? (
        <ActiveTaskCard key={item.task.id} task={item.task} now={now} onCancel={onCancel} />
      ) : (
        <TaskCenterRow
          key={item.task.id}
          task={item.task}
          onCancel={onCancel}
          onRetry={onRetry}
          onOpenResource={onOpenResource}
          onOpenDetail={onOpenDetail}
        />
      ),
    )}
  </>
);
