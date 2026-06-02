import React from 'react';
import { useTranslation } from 'react-i18next';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

export const AgentResultBody: React.FC<{ task: UnifiedTask }> = ({ task }) => {
  const { t } = useTranslation();
  const m = (task.metadata ?? {}) as {
    agent_input?: string | null;
    agent_output?: string | null;
    agent_prompt_tokens?: number | null;
    agent_completion_tokens?: number | null;
    agent_cost_cents?: number | null;
    agent_model?: string | null;
  };
  const total = (m.agent_prompt_tokens ?? 0) + (m.agent_completion_tokens ?? 0);

  return (
    <div className="p-4 space-y-3">
      {m.agent_input && <Block label={t('topbar.agentPrompt')} text={m.agent_input} tone="plain" />}
      <Block label={t('topbar.agentResponse')} text={m.agent_output || task.error_msg || ''} tone="accent" />

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
    <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">{label}</div>
    <div
      className={`text-[13px] leading-relaxed whitespace-pre-wrap break-words rounded p-3 ${
        tone === 'accent'
          ? 'text-zinc-100 bg-indigo-500/10 border-l-2 border-indigo-400'
          : 'text-zinc-300 bg-zinc-950/40 border border-zinc-800'
      }`}
    >
      {text || <span className="text-zinc-600">—</span>}
    </div>
  </div>
);

const Stat: React.FC<{ label: string; value: string; unit?: string }> = ({ label, value, unit }) => (
  <div className="flex flex-col">
    <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
    <span className="text-sm font-semibold text-zinc-100">
      {value}
      {unit && <span className="ml-0.5 text-[11px] text-zinc-500">{unit}</span>}
    </span>
  </div>
);
