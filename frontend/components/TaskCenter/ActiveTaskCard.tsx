import React, { useState } from 'react';
import { MessageSquarePlus, Pause, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  formatSpeed,
  taskTypeLabel,
  type UnifiedTask,
} from '../../contexts/TaskManagerContext';
import { TaskTypeIcon } from './TaskTypeIcon';
import { AgentNameBadge } from './AgentNameBadge';
import { formatElapsed } from './taskElapsed';
import { retryProgress, todoProgress } from './agentRunPresentation';
import { budgetState, contextGauge, currentStep, selectRunView } from './runView';
import { deliverSteer, InboxTargetEndedError, type InboxTargetKind } from '../../services/agentInboxService';
import { pauseIssue } from '../../services/issuesService';
import { controlErrorText } from '../Todolist/issueControlErrors';
import { notifyIssuePauseChanged } from './issuePauseSignal';

interface ActiveTaskCardProps {
  task: UnifiedTask;
  /** Shared clock (ms) ticked by the panel, so all cards update in step. */
  now: number;
  onCancel: (id: string) => void;
}

/**
 * Prominent card for a task that is executing right now — pulsing icon, live
 * elapsed time, RUNNING badge, and a progress bar. Used in the Task Center's
 * Active tab to make in-flight work (downloads, parses, AI/agent runs) obvious.
 */
export const ActiveTaskCard: React.FC<ActiveTaskCardProps> = ({ task, now, onCancel }) => {
  const { t } = useTranslation();

  const startedRaw = task.started_at || task.created_at;
  const startedMs = startedRaw ? Date.parse(startedRaw) : now;
  const elapsed = formatElapsed(now - startedMs);
  const pct = Math.max(task.progress || 0, 2);
  const todo = task.task_type === 'agent' ? todoProgress(task.metadata) : null;
  const retry = task.task_type === 'agent' ? retryProgress(task.metadata, now) : null;
  // Cockpit line (harness P4 T11): the same three gauges the issue page shows,
  // read through the selectors — turn/step, context %, budget colour.
  const view = task.task_type === 'agent' ? selectRunView(task.metadata) : null;
  const cur = currentStep(view);
  const gauge = contextGauge(view);
  const budget = budgetState(view);
  const steerTarget = steerTargetOf(task);
  // Phase 2a §2: target-level pause for issue-backed runs (the issue is the
  // target; conversations have no pause). Typed outcome, never a silent no-op.
  const [pauseState, setPauseState] = useState<'idle' | 'sending' | 'paused' | 'failed'>('idle');
  const [pauseError, setPauseError] = useState<string | null>(null);
  const pause = async () => {
    if (!steerTarget || steerTarget.kind !== 'issue' || pauseState === 'sending') return;
    setPauseState('sending');
    setPauseError(null);
    const issueId = Number(steerTarget.id);
    try {
      await pauseIssue(issueId);
      setPauseState('paused');
      // The Paused section (sibling in the Task Center) refetches on this.
      notifyIssuePauseChanged(issueId);
    } catch (err) {
      console.error('[ActiveTaskCard] pause failed', err);
      setPauseState('failed');
      setPauseError(controlErrorText(err, t));
    }
  };
  const [steerOpen, setSteerOpen] = useState(false);
  const [steerText, setSteerText] = useState('');
  const [steerState, setSteerState] = useState<'idle' | 'sending' | 'sent' | 'ended' | 'failed'>('idle');
  const sendSteer = async () => {
    if (!steerTarget || !steerText.trim() || steerState === 'sending') return;
    setSteerState('sending');
    try {
      await deliverSteer(steerTarget.kind, steerTarget.id, steerText.trim());
      setSteerState('sent');
      setSteerText('');
      setSteerOpen(false);
    } catch (err) {
      console.error('[ActiveTaskCard] steer failed', err);
      setSteerState(err instanceof InboxTargetEndedError ? 'ended' : 'failed');
    }
  };

  return (
    <div className="mx-3 my-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex items-start gap-3">
        {/* Pulsing status icon */}
        <div className="relative w-9 h-9 shrink-0">
          <span className="absolute inset-0 rounded-full bg-emerald-500/30 animate-ping" />
          <div className="relative w-9 h-9 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-sm">
            <TaskTypeIcon type={task.task_type} size={16} />
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-ink-100 truncate">{task.title}</span>
            <span className="flex items-center gap-1 shrink-0 px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 text-[10px] font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              {t('topbar.running').toUpperCase()}
            </span>
          </div>

          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-ink-500">{taskTypeLabel(task.task_type)}</span>
            {/* A running agent run is drawn here rather than by TaskCenterRow,
                so the badge has to exist on both or it vanishes mid-run. */}
            <AgentNameBadge task={task} />
            {task.subtitle && (
              <span className="text-[10px] text-ink-500 truncate">{task.subtitle}</span>
            )}
            <span className="text-[10px] text-emerald-500/80 ml-auto shrink-0 tabular-nums">{elapsed}</span>
            {steerTarget?.kind === 'issue' && (
              <button
                type="button"
                onClick={() => void pause()}
                disabled={pauseState === 'sending' || pauseState === 'paused'}
                data-testid="agent-pause"
                data-state={pauseState}
                className={`p-0.5 rounded transition-colors shrink-0 ${pauseState === 'failed' ? 'text-danger' : pauseState === 'paused' ? 'text-info' : 'text-ink-600 hover:text-info'} disabled:opacity-60`}
                title={pauseState === 'paused' ? t('taskCenter.pauseSent', 'Pausing at the next step') : t('taskCenter.pause', 'Pause')}
              >
                <Pause size={12} />
              </button>
            )}
            <button
              onClick={() => onCancel(task.id)}
              className="p-0.5 rounded text-ink-600 hover:text-danger transition-colors shrink-0"
              title={t('common.cancel')}
            >
              <X size={12} />
            </button>
          </div>

          {/* Agent step progress — only when the agent actually keeps a list.
              A run without one draws nothing here, not "0/0". */}
          {(todo || retry) && (
            <div className="mt-1 flex items-center gap-2 min-w-0" data-testid="agent-progress">
              {todo && (
                <span className="text-[10px] text-ink-400 truncate" data-testid="todo-progress">
                  <span className="tabular-nums">{todo.done}/{todo.total}</span>
                  {todo.label && <span className="text-ink-500"> · {todo.label}</span>}
                </span>
              )}
              {retry && (
                <span className="text-[10px] text-warn shrink-0 tabular-nums" data-testid="retry-progress">
                  {retry.waitingSeconds > 0
                    ? `Retry ${retry.attempt}/${retry.max} · waiting ${retry.waitingSeconds.toFixed(1)}s`
                    : `Retried ${retry.attempt}/${retry.max}`}
                </span>
              )}
            </div>
          )}

          {(cur || gauge || budget) && (
            <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-500 tabular-nums" data-testid="agent-cockpit">
              {cur?.step != null && <span>turn {cur.turn ?? 1} · step {cur.step}</span>}
              {gauge && <span className={gauge.used_pct >= 85 ? 'text-warn' : ''}>ctx {Math.round(gauge.used_pct)}%</span>}
              {budget && (
                <span className={budget.state === 'over' ? 'text-danger' : 'text-warn'} data-testid="agent-budget">
                  {t('topbar.budgetUsed', { pct: budget.pct })}
                </span>
              )}
            </div>
          )}

          {/* Pause outcome in words (same triple as steer below): the icon
              tint alone is not a message. */}
          {pauseState === 'paused' && <span data-testid="agent-pause-state" className="mt-1 block text-[10px] text-info">{t('taskCenter.pauseSent', 'Pausing at the next step')}</span>}
          {pauseState === 'failed' && <span data-testid="agent-pause-state" className="mt-1 block text-[10px] text-danger">{t('taskCenter.pauseFailed', 'Could not pause')}{pauseError ? ` — ${pauseError}` : ''}</span>}

          {/* Steer (harness P4 §1-③): a line to the running agent, read before
              its next step. Only when the run has an inbox target; the pause
              button in the header row is the target-level control (phase 2a). */}
          {steerTarget && (
            <div className="mt-1.5" data-testid="agent-steer">
              {steerOpen ? (
                <form
                  className="flex items-center gap-1.5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendSteer();
                  }}
                >
                  <input
                    data-testid="agent-steer-input"
                    value={steerText}
                    onChange={(e) => setSteerText(e.target.value)}
                    placeholder={t('topbar.steerPlaceholder', 'Steer the running agent…')}
                    className="flex-1 min-w-0 rounded border border-ink-700 bg-ink-900 px-2 py-0.5 text-[11px] text-ink-200"
                    autoFocus
                  />
                  <button type="submit" disabled={steerState === 'sending' || !steerText.trim()} className="text-[11px] text-ok hover:underline disabled:opacity-50">
                    {t('topbar.steerSend', 'Send')}
                  </button>
                  <button type="button" onClick={() => setSteerOpen(false)} className="text-[11px] text-ink-600 hover:underline">
                    {t('common.cancel')}
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  data-testid="agent-steer-open"
                  onClick={() => {
                    setSteerState('idle');
                    setSteerOpen(true);
                  }}
                  className="inline-flex items-center gap-1 text-[10px] text-ink-500 hover:text-ink-200"
                >
                  <MessageSquarePlus size={11} /> {t('topbar.steer', 'Steer')}
                </button>
              )}
              {steerState === 'sent' && <span className="ml-2 text-[10px] text-ok">{t('topbar.steerSent', 'Sent — read before the next step')}</span>}
              {steerState === 'ended' && <span className="ml-2 text-[10px] text-warn">{t('topbar.steerEnded', 'Run already ended')}</span>}
              {steerState === 'failed' && <span className="ml-2 text-[10px] text-danger">{t('topbar.steerFailed', 'Could not send')}</span>}
            </div>
          )}

          {/* Progress */}
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-ink-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            {task.progress > 0 && (
              <span className="text-[10px] text-ink-400 shrink-0 tabular-nums">{task.progress}%</span>
            )}
            {task.speed != null && task.speed > 0 && (
              <span className="text-[10px] text-ink-600 shrink-0">{formatSpeed(task.speed)}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};


/** Where a steer from this card goes: the issue the run serves, else its
 * conversation. Null for non-agent tasks and runs with no target. */
export function steerTargetOf(task: UnifiedTask): { kind: InboxTargetKind; id: string } | null {
  if (task.task_type !== 'agent' || task.status !== 'processing') return null;
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const issue = md.agent_issue_id;
  if (typeof issue === 'string' && issue) return { kind: 'issue', id: issue };
  const conv = md.agent_conversation_id;
  if (typeof conv === 'string' && conv) return { kind: 'conversation', id: conv };
  return null;
}
