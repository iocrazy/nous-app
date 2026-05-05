/**
 * Paperclip-style Chat tab — streamed timeline of issue messages,
 * agent runs, and system status changes (A8 UI).
 */

import React from 'react';
import type { IssueMessage, AgentRef, IssueStatus } from './types';
import { STATUS_LABEL, STATUS_COLOR, IssueStatusIcon } from './IssueStatusIcon';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueChatThreadProps {
  messages: IssueMessage[];
  /** When set, a small "Board" pill is appended to agent_run headers for board-signoff context. */
  boardSignoff?: string;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-zinc-600', size = 22 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-white ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

function isAgentRef(a: unknown): a is AgentRef {
  return !!a && typeof a === 'object' && 'slug' in (a as Record<string, unknown>);
}

function formatDuration(sec?: number): string {
  if (!sec) return '';
  if (sec < 60) return `${sec} second${sec === 1 ? '' : 's'}`;
  const min = Math.floor(sec / 60);
  return `${min} minute${min === 1 ? '' : 's'}`;
}

const SystemStatusEvent: React.FC<{ msg: IssueMessage }> = ({ msg }) => {
  const from = msg.from_status as IssueStatus | undefined;
  const to = msg.to_status as IssueStatus | undefined;
  const author = msg.author && 'name' in msg.author ? msg.author.name : 'System';
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

const AgentRunEvent: React.FC<{ msg: IssueMessage; boardSignoff?: string }> = ({ msg, boardSignoff }) => {
  const author = isAgentRef(msg.author) ? msg.author : null;
  const initials = (author?.name ?? '·').slice(0, 2).toUpperCase();
  return (
    <div className="my-3">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={author?.avatar_color} />
        <span className="text-xs font-medium text-zinc-200">{author?.name ?? 'Agent'}</span>
        {msg.duration_seconds !== undefined && (
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
      <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/50 p-3 text-[13px] text-zinc-300 leading-relaxed whitespace-pre-wrap break-words">
        {msg.body}
      </div>
    </div>
  );
};

const CommentEvent: React.FC<{ msg: IssueMessage }> = ({ msg }) => {
  const author = msg.author && 'name' in msg.author ? msg.author : null;
  const initials = (author?.name ?? '·').slice(0, 2).toUpperCase();
  const color = isAgentRef(author) ? author.avatar_color : 'bg-indigo-500';
  return (
    <div className="my-3">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={color} />
        <span className="text-xs font-medium text-zinc-200">{author?.name ?? 'You'}</span>
        <span className="text-[11px] text-zinc-500">commented · {relativeTime(msg.created_at)}</span>
      </div>
      <div className="ml-7 rounded border border-zinc-800/80 bg-zinc-900/30 p-3 text-[13px] text-zinc-200 leading-relaxed whitespace-pre-wrap break-words">
        {msg.body}
      </div>
    </div>
  );
};

export const IssueChatThread: React.FC<IssueChatThreadProps> = ({ messages, boardSignoff }) => {
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
        if (m.kind === 'system_status') return <SystemStatusEvent key={m.id} msg={m} />;
        if (m.kind === 'agent_run') return <AgentRunEvent key={m.id} msg={m} boardSignoff={boardSignoff} />;
        return <CommentEvent key={m.id} msg={m} />;
      })}
    </div>
  );
};
