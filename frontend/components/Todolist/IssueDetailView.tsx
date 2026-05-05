/**
 * Paperclip-style issue detail view (A8.3 wired to real backend).
 *
 * Fetches messages on mount, subscribes to Realtime issue_messages
 * inserts so new comments / status changes / agent runs stream in
 * without manual refresh.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ChevronLeft, MoreHorizontal, AlignLeft, Paperclip, FileText, Plus,
  MessageSquare, Activity, Link2,
} from 'lucide-react';
import type { UiIssue, AgentRef } from './types';
import type { IssueMessage } from '../../services/issueMessageService';
import { IssueStatusIcon, PriorityIcon } from './IssueStatusIcon';
import { IssueChatThread } from './IssueChatThread';
import { IssueReplyBox } from './IssueReplyBox';
import { listIssueMessages, postIssueMessage } from '../../services/issueMessageService';
import { getSupabaseClient } from '../../supabaseClient';
import { useToast } from '../Toast';

interface IssueDetailViewProps {
  issue: UiIssue;
  agents: AgentRef[];
  agentsById: Record<string, AgentRef>;
  selfUserId?: string;
}

type DetailTab = 'chat' | 'activity' | 'related';

export const IssueDetailView: React.FC<IssueDetailViewProps> = ({ issue, agents, agentsById, selfUserId }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [tab, setTab] = useState<DetailTab>('chat');
  const [messages, setMessages] = useState<IssueMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { addToast } = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listIssueMessages(issue.id);
      setMessages(list.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load messages');
    } finally {
      setLoading(false);
    }
  }, [issue.id]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Realtime: append new issue_messages rows on INSERT for this issue.
  useEffect(() => {
    const supa = getSupabaseClient();
    if (!supa) return;
    const channel = supa
      .channel(`issue-messages-${issue.id}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'issue_messages',
          filter: `issue_id=eq.${issue.id}`,
        },
        (payload) => {
          const row = payload.new as IssueMessage | undefined;
          if (!row) return;
          setMessages((prev) => {
            if (prev.some((m) => m.id === row.id)) return prev;
            return [...prev, row];
          });
        },
      )
      .subscribe();
    return () => { void supa.removeChannel(channel); };
  }, [issue.id]);

  const handleReply = async (body: string, agentId: string | null) => {
    try {
      const resp = await postIssueMessage(issue.id, { body, agent_id: agentId ?? undefined });
      setMessages((prev) => {
        const next = [...prev];
        if (!next.some((m) => m.id === resp.comment.id)) next.push(resp.comment);
        if (resp.agent_run && !next.some((m) => m.id === resp.agent_run!.id)) next.push(resp.agent_run);
        return next;
      });
      addToast(agentId ? 'Reply posted; agent dispatched' : 'Comment posted', 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Send failed', 'error');
      throw err; // signal failure to keep textarea content
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800/80 text-xs text-zinc-500">
        <Link to={`/team/${teamId}/todolist`} className="inline-flex items-center gap-1 hover:text-zinc-300">
          <ChevronLeft size={12} />
          Issues
        </Link>
        <span className="text-zinc-600">/</span>
        <span className="text-zinc-300 truncate">{issue.title}</span>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-6">
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

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-5">
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60" disabled>
              <Plus size={12} /> New Sub-issue
            </button>
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60" disabled>
              <Paperclip size={12} /> Upload attachment
            </button>
            <button className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60" disabled>
              <FileText size={12} /> New document
            </button>
          </div>

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
            <span className="ml-auto text-[10px] text-zinc-600 pr-2">{messages.length} message{messages.length === 1 ? '' : 's'}</span>
          </div>

          {tab === 'chat' && (
            <>
              {error && (
                <div className="mt-4 mx-2 px-3 py-2 rounded bg-rose-500/10 text-rose-300 text-xs ring-1 ring-rose-500/30">
                  {error}
                </div>
              )}
              {loading && messages.length === 0
                ? <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">Loading messages…</div>
                : <IssueChatThread messages={messages} agentsById={agentsById} selfUserId={selfUserId} />}
            </>
          )}
          {tab === 'activity' && (
            <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">
              Activity timeline lands once status / property events are split out from the chat thread.
            </div>
          )}
          {tab === 'related' && (
            <div className="text-sm text-zinc-500 italic px-4 py-12 text-center">
              Related work (sub-issues, blocking links, attached documents) will surface here.
            </div>
          )}
        </div>
      </div>

      <IssueReplyBox
        agents={agents}
        defaultAgentId={issue.assignee?.id ?? null}
        onSubmit={handleReply}
      />
    </div>
  );
};
