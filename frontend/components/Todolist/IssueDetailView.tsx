/**
 * Paperclip-style issue detail view (A8 UI scaffold).
 *
 * Header: breadcrumb + ID badge + project pill + actions
 * Title block: large title + description
 * Action buttons: + New Sub-issue / Upload attachment / + New document
 * Tabs: Chat (default) / Activity / Related work
 * Reply box pinned at bottom
 */

import React, { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ChevronLeft, AlertTriangle, MoreHorizontal, AlignLeft,
  Paperclip, FileText, Plus, MessageSquare, Activity, Link2,
} from 'lucide-react';
import type { Issue, IssueMessage } from './types';
import { IssueStatusIcon, STATUS_LABEL, PriorityIcon } from './IssueStatusIcon';
import { IssueChatThread } from './IssueChatThread';
import { IssueReplyBox } from './IssueReplyBox';
import { useToast } from '../Toast';

interface IssueDetailViewProps {
  issue: Issue;
  messages: IssueMessage[];
}

type DetailTab = 'chat' | 'activity' | 'related';

export const IssueDetailView: React.FC<IssueDetailViewProps> = ({ issue, messages }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [tab, setTab] = useState<DetailTab>('chat');
  const { addToast } = useToast();

  const handleReply = (body: string, agentId: string | null) => {
    addToast(
      agentId
        ? `(mock) Replied + dispatched to agent ${agentId.slice(0, 12)}…`
        : '(mock) Comment posted',
      'success',
    );
  };

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800/80 text-xs text-zinc-500">
        <Link to={`/team/${teamId}/todolist`} className="inline-flex items-center gap-1 hover:text-zinc-300">
          <ChevronLeft size={12} />
          Issues
        </Link>
        <span className="text-zinc-600">/</span>
        <span className="text-zinc-300 truncate">{issue.title}</span>
      </div>

      {/* Scrollable detail body */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-6">

          {/* Header card */}
          <div className="flex items-center gap-2 mb-3">
            <IssueStatusIcon status={issue.status} size={14} />
            <span className="font-mono text-[10px] text-zinc-500 uppercase tracking-wider">{issue.identifier}</span>
            <span title={issue.priority}>
              <PriorityIcon priority={issue.priority} />
            </span>
            {issue.project && (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">
                <span className={`w-1.5 h-1.5 rounded-full ${issue.project.color ?? 'bg-zinc-500'}`} />
                {issue.project.name}
              </span>
            )}
            <div className="ml-auto flex items-center gap-1">
              <button className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded" title="Properties">
                <AlignLeft size={13} />
              </button>
              <button className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded" title="More">
                <MoreHorizontal size={13} />
              </button>
            </div>
          </div>

          <h1 className="text-xl font-semibold text-zinc-100 leading-tight">{issue.title}</h1>
          {issue.description && (
            <p className="mt-2 text-sm text-zinc-400 leading-relaxed whitespace-pre-wrap">{issue.description}</p>
          )}

          {/* Action buttons row */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-5">
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60">
              <Plus size={12} /> New Sub-issue
            </button>
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60">
              <Paperclip size={12} /> Upload attachment
            </button>
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60">
              <FileText size={12} /> New document
            </button>
          </div>

          {/* Tabs */}
          <div className="flex items-center border-b border-zinc-800/80 mt-6">
            {(['chat', 'activity', 'related'] as DetailTab[]).map((t) => {
              const label = t === 'chat' ? 'Chat' : t === 'activity' ? 'Activity' : 'Related work';
              const Icon = t === 'chat' ? MessageSquare : t === 'activity' ? Activity : Link2;
              const active = tab === t;
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition ${
                    active
                      ? 'border-indigo-400 text-zinc-100'
                      : 'border-transparent text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Icon size={12} />
                  {label}
                </button>
              );
            })}
            <span className="ml-auto text-[10px] text-zinc-600 pr-2">Jump to latest</span>
          </div>

          {/* Tab body */}
          {tab === 'chat' && <IssueChatThread messages={messages} boardSignoff="BO" />}
          {tab === 'activity' && (
            <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">
              Activity timeline lands once the data layer is wired. Status changes, properties edits, and assignments will show here separately from chat.
            </div>
          )}
          {tab === 'related' && (
            <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">
              Related work (sub-issues, blocking links, attached documents) will surface here.
            </div>
          )}
        </div>
      </div>

      {/* Reply box */}
      <IssueReplyBox defaultAgent={issue.assignee} onSubmit={handleReply} />
    </div>
  );
};
