/**
 * Paperclip-style Chat tab — streamed timeline of issue messages,
 * agent runs, and system status changes (A8.3 wired to real backend).
 *
 * Consumes IssueMessage rows directly from issueMessageService. Author
 * resolution (uuid → display name + avatar color) happens here via the
 * agentsMap / userLabel passed in by the parent page.
 */

import React, { useState } from 'react';
import { Zap } from 'lucide-react';
import type { IssueMessage, AgentLivenessState } from '../../services/issueMessageService';
import { simulateAgentRunComplete } from '../../services/issueMessageService';
import type { AgentRef } from './types';
import { STATUS_LABEL, STATUS_COLOR, IssueStatusIcon } from './IssueStatusIcon';
import type { IssueStatus } from '../../services/issuesService';
import { relativeTime } from '../../utils/taskDisplay';
import { useToast } from '../Toast';
import { useElapsedSeconds } from '../../hooks/useElapsedSeconds';

const LIVENESS_VISUAL: Record<AgentLivenessState, { dot: string; label: string; tooltip: string }> = {
  running:   { dot: 'bg-emerald-500', label: 'running',   tooltip: 'Agent is making progress' },
  silent:    { dot: 'bg-amber-400',   label: 'silent',    tooltip: 'No useful action recently — watching' },
  stuck:     { dot: 'bg-orange-500',  label: 'stuck',     tooltip: 'Stuck long enough to attempt continuation' },
  dead:      { dot: 'bg-rose-500',    label: 'dead',      tooltip: 'Marked dead by liveness scanner' },
  cancelled: { dot: 'bg-zinc-500',    label: 'cancelled', tooltip: 'Cancelled by user / system' },
};

const LivenessPill: React.FC<{ state: AgentLivenessState }> = ({ state }) => {
  const v = LIVENESS_VISUAL[state] ?? LIVENESS_VISUAL.running;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-900/60 ring-1 ring-zinc-800 text-[12px] text-zinc-300"
      title={v.tooltip}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${v.dot}`} />
      {v.label}
    </span>
  );
};

interface IssueChatThreadProps {
  messages: IssueMessage[];
  agentsById: Record<string, AgentRef>;
  /** Display label for the current authenticated user (so own comments show "You"). */
  selfUserId?: string;
  /**
   * Live token-by-token text accumulating from the active agent turn.
   * When non-empty a transient streaming bubble is rendered at the bottom.
   * Cleared (set to '') by the parent when the `message` event arrives.
   */
  streamingText?: string;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-zinc-600', size = 22 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[12px] font-semibold text-white ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

function formatDuration(sec?: number | null): string {
  if (sec === null || sec === undefined) return '';
  if (sec < 60) return `${sec} second${sec === 1 ? '' : 's'}`;
  const min = Math.floor(sec / 60);
  return `${min} minute${min === 1 ? '' : 's'}`;
}

const SystemStatusEvent: React.FC<{ msg: IssueMessage; selfUserId?: string }> = ({ msg, selfUserId }) => {
  const from = msg.from_status as IssueStatus | undefined;
  const to = msg.to_status as IssueStatus | undefined;
  const isSelf = msg.author_user_id && msg.author_user_id === selfUserId;
  const author = isSelf ? 'You' : msg.author_user_id ? `User ${msg.author_user_id.slice(0, 6)}` : 'System';
  return (
    <div
      data-testid="system-status-row"
      className="text-center text-[11px] text-zinc-500 italic my-2"
    >
      <span className="text-zinc-400 not-italic font-medium">{author}</span>
      {' '}updated this task —{' '}
      <span className="not-italic">STATUS</span>{' '}
      {from && (
        <span className={`${STATUS_COLOR[from]} not-italic font-medium inline-flex items-center gap-0.5`}>
          <IssueStatusIcon status={from} size={10} />
          {STATUS_LABEL[from].toLowerCase()}
        </span>
      )}
      {' '}<span className="text-zinc-600 not-italic">→</span>{' '}
      {to && (
        <span className={`${STATUS_COLOR[to]} not-italic font-medium inline-flex items-center gap-0.5`}>
          <IssueStatusIcon status={to} size={10} />
          {STATUS_LABEL[to].toLowerCase()}
        </span>
      )}
    </div>
  );
};

const AgentRunEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef> }> = ({ msg, agentsById }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const initials = (agent?.name ?? '·').slice(0, 2).toUpperCase();
  const boardSignoff = (msg.meta?.board_signoff as string | undefined);
  // Liveness comes either inlined on the row (future), or via meta.status
  // emitted by the bridge trigger (mig 206). Display it as a pill so users
  // can see "agent is silent / stuck / dead" without leaving the chat.
  const liveness = (msg.liveness_state as AgentLivenessState | undefined)
    ?? (msg.meta?.liveness_state as AgentLivenessState | undefined)
    ?? null;
  const metaStatus = msg.meta?.status as string | undefined;
  const errorCode = msg.meta?.error_code as string | undefined;
  const isRunning = metaStatus === 'running';
  // Live tick for in-flight runs. Falls back to msg.created_at as the
  // timestamp origin because started_at is not yet surfaced on IssueMessage
  // (agent_runs.started_at exists in DB but isn't in the REST response).
  const elapsedLive = useElapsedSeconds(msg.created_at, { enabled: isRunning });
  const { addToast } = useToast();
  const [simulating, setSimulating] = useState(false);

  const onSimulate = async () => {
    if (!msg.agent_run_id || simulating) return;
    setSimulating(true);
    try {
      await simulateAgentRunComplete(msg.issue_id, msg.agent_run_id);
      addToast('Simulated agent finish — row should update shortly', 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Simulate failed', 'error');
    } finally {
      setSimulating(false);
    }
  };
  return (
    <div className="my-3">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={agent?.avatar_color} />
        <span className="text-xs font-medium text-zinc-200">{agent?.name ?? 'Agent'}</span>
        {isRunning ? (
          <span className="text-[12px] text-zinc-500">working for {formatDuration(elapsedLive)}</span>
        ) : msg.duration_seconds != null ? (
          <span className="text-[12px] text-zinc-500">worked for {formatDuration(msg.duration_seconds)}</span>
        ) : null}
        {liveness && <LivenessPill state={liveness} />}
        {metaStatus && metaStatus !== 'completed' && (
          <span className="text-[12px] text-zinc-500 italic">({metaStatus})</span>
        )}
        {isRunning && msg.agent_run_id && (
          <button
            onClick={onSimulate}
            disabled={simulating}
            className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[12px] rounded bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30 hover:bg-amber-500/20 disabled:opacity-50"
            title="Dev: simulate the agent finishing this run"
          >
            <Zap size={9} /> {simulating ? 'Sim…' : 'Simulate finish'}
          </button>
        )}
        {errorCode && (
          <span className="px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/30 text-[12px] font-mono" title="error_code">
            {errorCode}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-2 text-[12px] text-zinc-500">
          {boardSignoff && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 text-[12px]">
              Board <span className="font-mono text-[12px]">{boardSignoff}</span>
            </span>
          )}
          {relativeTime(msg.created_at)}
        </span>
      </div>
      {msg.body && (
        <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/50 p-3 text-[14px] text-zinc-300 leading-relaxed whitespace-pre-wrap break-words">
          {msg.body}
        </div>
      )}
    </div>
  );
};

const CommentEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef>; selfUserId?: string }> = ({ msg, agentsById, selfUserId }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const isSelf = msg.author_user_id && msg.author_user_id === selfUserId;
  const displayName = agent?.name ?? (isSelf ? 'You' : msg.author_user_id ? `User ${msg.author_user_id.slice(0, 6)}` : 'Anonymous');
  const initials = displayName.slice(0, 2).toUpperCase();
  const color = agent?.avatar_color ?? (isSelf ? 'bg-indigo-500' : 'bg-zinc-600');
  return (
    <div
      data-testid="comment-row"
      className={`flex my-2 ${isSelf ? 'justify-end' : 'justify-start'}`}
    >
      <div
        data-testid="comment-bubble"
        className={`max-w-[80%] rounded-lg px-3 py-2 ${
          isSelf
            ? 'bg-blue-600/15 border border-blue-700/40 text-zinc-100'
            : 'bg-zinc-800 border border-zinc-700 text-zinc-200'
        }`}
      >
        <div className="flex items-center gap-2 mb-1.5">
          <AgentAvatar initials={initials} color={color} />
          <span className="text-xs font-medium">{displayName}</span>
          <span className="text-[12px] text-zinc-500">commented · {relativeTime(msg.created_at)}</span>
        </div>
        {msg.body && (
          <div className="rounded text-[14px] leading-relaxed whitespace-pre-wrap break-words">
            {msg.body}
          </div>
        )}
      </div>
    </div>
  );
};

/** Blinking text cursor shown while the agent is streaming. */
const StreamingCursor: React.FC = () => (
  <span
    className="inline-block w-[2px] h-[1em] bg-zinc-300 ml-0.5 align-middle animate-pulse"
    aria-hidden="true"
  />
);

/** Transient assistant bubble rendered while tokens are arriving. */
const StreamingBubble: React.FC<{ text: string }> = ({ text }) => (
  <div className="my-3">
    <div className="flex items-center gap-2 mb-1.5">
      <AgentAvatar initials="AI" color="bg-indigo-600" />
      <span className="text-xs font-medium text-zinc-200">Agent</span>
      <span className="text-[12px] text-zinc-500 italic">streaming…</span>
    </div>
    <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/50 p-3 text-[14px] text-zinc-300 leading-relaxed whitespace-pre-wrap break-words">
      {text}
      <StreamingCursor />
    </div>
  </div>
);

export const IssueChatThread: React.FC<IssueChatThreadProps> = ({ messages, agentsById, selfUserId, streamingText }) => {
  const hasStreaming = typeof streamingText === 'string' && streamingText.length > 0;

  if (messages.length === 0 && !hasStreaming) {
    return (
      <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">
        No activity yet. Reply below to start the conversation or dispatch the issue to an agent.
      </div>
    );
  }
  return (
    <div className="px-4 py-3">
      {messages.map((m) => {
        if (m.kind === 'system_status') return <SystemStatusEvent key={m.id} msg={m} selfUserId={selfUserId} />;
        if (m.kind === 'agent_run')     return <AgentRunEvent key={m.id} msg={m} agentsById={agentsById} />;
        return <CommentEvent key={m.id} msg={m} agentsById={agentsById} selfUserId={selfUserId} />;
      })}
      {hasStreaming && <StreamingBubble text={streamingText as string} />}
    </div>
  );
};
