/**
 * Paperclip-style Chat tab — streamed timeline of issue messages,
 * agent runs, and system status changes (A8.3 wired to real backend).
 *
 * Consumes IssueMessage rows directly from issueMessageService. Author
 * resolution (uuid → display name + avatar color) happens here via the
 * agentsMap / userLabel passed in by the parent page.
 */

import React from 'react';
import type { IssueMessage } from '../../services/issueMessageService';
import type { AgentRef } from './types';
import { STATUS_LABEL, STATUS_COLOR, IssueStatusIcon } from './IssueStatusIcon';
import type { IssueStatus } from '../../services/issuesService';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueChatThreadProps {
  messages: IssueMessage[];
  agentsById: Record<string, AgentRef>;
  /** Display label for the current authenticated user (so own comments show "You"). */
  selfUserId?: string;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-zinc-600', size = 22 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-white ${color}`}
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
    <div className="flex items-center gap-2 px-2 py-1.5 my-1 text-[11px] text-zinc-500">
      <span className="font-medium text-zinc-400">{author}</span>
      <span>updated this task · {relativeTime(msg.created_at)}</span>
      <span className="ml-auto inline-flex items-center gap-1.5">
        <span className="text-zinc-500">STATUS</span>
        {from && (
          <span className={`${STATUS_COLOR[from]} inline-flex items-center gap-1`}>
            <IssueStatusIcon status={from} size={10} />
            {STATUS_LABEL[from].toLowerCase()}
          </span>
        )}
        <span className="text-zinc-600">→</span>
        {to && (
          <span className={`${STATUS_COLOR[to]} inline-flex items-center gap-1`}>
            <IssueStatusIcon status={to} size={10} />
            {STATUS_LABEL[to].toLowerCase()}
          </span>
        )}
      </span>
    </div>
  );
};

const AgentRunEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef> }> = ({ msg, agentsById }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const initials = (agent?.name ?? '·').slice(0, 2).toUpperCase();
  const boardSignoff = (msg.meta?.board_signoff as string | undefined);
  return (
    <div className="my-3">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={agent?.avatar_color} />
        <span className="text-xs font-medium text-zinc-200">{agent?.name ?? 'Agent'}</span>
        {msg.duration_seconds != null && (
          <span className="text-[11px] text-zinc-500">worked for {formatDuration(msg.duration_seconds)}</span>
        )}
        <span className="ml-auto inline-flex items-center gap-2 text-[11px] text-zinc-500">
          {boardSignoff && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 text-[10px]">
              Board <span className="font-mono text-[9px]">{boardSignoff}</span>
            </span>
          )}
          {relativeTime(msg.created_at)}
        </span>
      </div>
      {msg.body && (
        <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/50 p-3 text-[13px] text-zinc-300 leading-relaxed whitespace-pre-wrap break-words">
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
    <div className="my-3">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={color} />
        <span className="text-xs font-medium text-zinc-200">{displayName}</span>
        <span className="text-[11px] text-zinc-500">commented · {relativeTime(msg.created_at)}</span>
      </div>
      {msg.body && (
        <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/30 p-3 text-[13px] text-zinc-200 leading-relaxed whitespace-pre-wrap break-words">
          {msg.body}
        </div>
      )}
    </div>
  );
};

export const IssueChatThread: React.FC<IssueChatThreadProps> = ({ messages, agentsById, selfUserId }) => {
  if (messages.length === 0) {
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
    </div>
  );
};
