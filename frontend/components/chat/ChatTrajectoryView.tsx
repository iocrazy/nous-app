/**
 * The chat's Trajectory tab (harness P4 T10): the same conversation, seen as
 * runs — one block per assistant turn that has a run, each drawn by the shared
 * TrajectoryRenderer (steps do not stack; only the live step is open). The
 * transcript is fetched by useRunToolActivity, which caches settled runs, so
 * flipping Chat ⇄ Trajectory never refetches a finished run.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

import type { AIChatMessage } from '../../types';
import { TrajectoryRenderer } from '../agentActivity/TrajectoryRenderer';
import { useRunToolActivity } from '../agentActivity/useRunToolActivity';
import { chatRunId } from './chatMessageMeta';

const RunBlock: React.FC<{ msg: AIChatMessage; runId: string; isRunning: boolean; index: number }> = ({
  msg,
  runId,
  isRunning,
  index,
}) => {
  const { t } = useTranslation();
  const { events, loaded } = useRunToolActivity(runId, isRunning);
  const tokens = (msg.prompt_tokens ?? 0) + (msg.completion_tokens ?? 0);
  return (
    <section className="rounded-lg border border-ink-800/80 bg-ink-950/40" data-testid="chat-run-block" data-run-id={runId}>
      <header className="flex items-center gap-2 px-2.5 py-1.5 text-[11px] text-ink-500 border-b border-ink-800/60">
        <span className="font-mono text-ink-400">{t('chat.view.run', 'Run')} #{index + 1}</span>
        {tokens > 0 && <span className="tabular-nums">{tokens} tok</span>}
        <span className="ml-auto tabular-nums">{new Date(msg.created_at).toLocaleTimeString()}</span>
      </header>
      <div className="px-1 py-1">
        {events.length === 0 ? (
          <div className="px-2 py-1.5 text-[11px] text-ink-600">
            {loaded ? t('chat.view.noTrajectory', 'No recorded steps for this run') : t('trajectory.thinking', 'Thinking…')}
          </div>
        ) : (
          <TrajectoryRenderer events={events} isRunning={isRunning} hideWhenEmpty={false} />
        )}
      </div>
    </section>
  );
};

export interface ChatTrajectoryViewProps {
  messages: AIChatMessage[];
  /** True while a turn is streaming — only the LAST run may be live. */
  isRunning: boolean;
}

export const ChatTrajectoryView: React.FC<ChatTrajectoryViewProps> = ({ messages, isRunning }) => {
  const { t } = useTranslation();
  const runs = messages
    .filter((m) => m.role === 'assistant')
    .map((m) => ({ msg: m, runId: chatRunId(m) }))
    .filter((r): r is { msg: AIChatMessage; runId: string } => r.runId !== null);
  if (runs.length === 0) {
    return <div className="px-3 py-6 text-center text-[12px] text-ink-500">{t('chat.view.noRuns', 'No agent runs in this conversation yet')}</div>;
  }
  return (
    <div className="flex flex-col gap-2" data-testid="chat-trajectory">
      {runs.map((r, i) => (
        <RunBlock key={r.runId} msg={r.msg} runId={r.runId} isRunning={isRunning && i === runs.length - 1} index={i} />
      ))}
    </div>
  );
};

export default ChatTrajectoryView;
