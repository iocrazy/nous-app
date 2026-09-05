import React from 'react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import { TrajectoryRenderer } from '../../agentActivity/TrajectoryRenderer';
import { useRunToolActivity } from '../../agentActivity/useRunToolActivity';
import type { AgentTodoSnapshot } from '../agentRunPresentation';
import { selectRunView, stepProgress } from '../runView';

export const AgentResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const m = (task.metadata ?? {}) as {
    agent_input?: string | null;
    agent_output?: string | null;
    agent_prompt_tokens?: number | null;
    agent_completion_tokens?: number | null;
    agent_cost_cents?: number | null;
    agent_model?: string | null;
    todos?: AgentTodoSnapshot | null;
  };
  const total = (m.agent_prompt_tokens ?? 0) + (m.agent_completion_tokens ?? 0);
  const todos = Array.isArray(m.todos?.todos) ? m.todos!.todos : [];
  // view-first (mig 453 fold), legacy todo counts as the transition fallback
  const step = stepProgress(selectRunView(task.metadata as Record<string, unknown>));
  const counts = step ? { completed: step.done, total: step.total } : m.todos?.counts;
  // The run's trajectory — same renderer as the issue timeline and the chat's
  // Trajectory tab. task.id IS the agent_runs id for an agent task.
  const { events } = useRunToolActivity(task.id, task.status === 'processing');

  return (
    <div className="p-4 space-y-3">
      {m.agent_input && <Block label={t('topbar.agentPrompt')} text={m.agent_input} tone="plain" />}
      <Block label={t('topbar.agentResponse')} text={m.agent_output || task.error_msg || ''} tone="accent" />

      {todos.length > 0 && (
        <div data-testid="agent-todo-list">
          <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">
            {t('topbar.agentSteps', 'Steps')}
            {counts && (
              <span className="ml-1 tabular-nums normal-case tracking-normal">
                {counts.completed}/{counts.total}
              </span>
            )}
          </div>
          <ul className="space-y-1 rounded border border-ink-800 bg-ink-950/40 p-2">
            {todos.map((item) => (
              <li key={item.id} className="flex items-start gap-2 text-[12px]" data-status={item.status}>
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                    item.status === 'completed'
                      ? 'bg-ok'
                      : item.status === 'in_progress'
                        ? 'bg-info animate-pulse'
                        : 'bg-ink-600'
                  }`}
                />
                <span
                  className={
                    item.status === 'completed'
                      ? 'text-ink-500 line-through'
                      : item.status === 'in_progress'
                        ? 'text-ink-100'
                        : 'text-ink-400'
                  }
                >
                  {item.status === 'in_progress' && item.active_form ? item.active_form : item.content}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {events.length > 0 && (
        <div data-testid="agent-trajectory">
          <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">{t('topbar.agentTrajectory', 'Trajectory')}</div>
          <div className="rounded border border-ink-800 bg-ink-950/40 p-1">
            <TrajectoryRenderer events={events} isRunning={task.status === 'processing'} hideWhenEmpty={false} />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-4 pt-1">
        {m.agent_model && <Stat label={t('topbar.agentModel')} value={m.agent_model} />}
        {m.agent_prompt_tokens != null && <Stat label="prompt" value={`${m.agent_prompt_tokens}`} unit="tok" />}
        {m.agent_completion_tokens != null && (
          <Stat label="completion" value={`${m.agent_completion_tokens}`} unit="tok" />
        )}
        {total > 0 && <Stat label="total" value={`${total}`} unit="tok" />}
        {m.agent_cost_cents != null && <Stat label="cost" value={`${m.agent_cost_cents}`} unit="¢" />}
      </div>
    </div>
  );
};

const Block: React.FC<{ label: string; text: string; tone: 'plain' | 'accent' }> = ({
  label,
  text,
  tone,
}) => (
  <div>
    <div className="text-[10px] uppercase tracking-wide text-ink-500 mb-1">{label}</div>
    <div
      className={`text-[13px] leading-relaxed whitespace-pre-wrap break-words rounded p-3 ${
        tone === 'accent'
          ? 'text-ink-100 bg-[var(--accent-soft)] border-l-2 border-indigo-400'
          : 'text-ink-300 bg-ink-950/40 border border-ink-800'
      }`}
    >
      {text || <span className="text-ink-600">—</span>}
    </div>
  </div>
);

const Stat: React.FC<{ label: string; value: string; unit?: string }> = ({ label, value, unit }) => (
  <div className="flex flex-col">
    <span className="text-[10px] uppercase tracking-wide text-ink-500">{label}</span>
    <span className="text-sm font-semibold text-ink-100">
      {value}
      {unit && <span className="ml-0.5 text-[11px] text-ink-500">{unit}</span>}
    </span>
  </div>
);
