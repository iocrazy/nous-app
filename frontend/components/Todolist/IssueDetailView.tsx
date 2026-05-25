/**
 * Paperclip-style issue detail view (A8.3 wired to real backend).
 *
 * Fetches messages on mount, subscribes to Realtime issue_messages
 * inserts so new comments / status changes / agent runs stream in
 * without manual refresh.
 *
 * Agent replies now stream token-by-token over the /ws/issue/{id}
 * WebSocket (replaces polling from commit 3453a990).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ChevronLeft, MoreHorizontal, AlignLeft, Paperclip, FileText, Plus,
  MessageSquare, Activity, Link2, Bot,
} from 'lucide-react';
import type { UiIssue, AgentRef } from './types';
import type { IssueMessage } from '../../services/issueMessageService';
import { IssueStatusIcon, PriorityIcon } from './IssueStatusIcon';
import { IssueChatThread } from './IssueChatThread';
import { IssueActivityTab } from './IssueActivityTab';
import { IssueRelatedTab } from './IssueRelatedTab';
import { IssueReplyBox } from './IssueReplyBox';
import { listIssueMessages, postIssueMessage } from '../../services/issueMessageService';
import { dispatchIssue } from '../../services/issuesService';
import { openIssueChatSocket } from '../../services/issueChatSocket';
import { getSupabaseClient } from '../../supabaseClient';
import { useToast } from '../Toast';

interface IssueDetailViewProps {
  issue: UiIssue;
  agents: AgentRef[];
  agentsById: Record<string, AgentRef>;
  selfUserId?: string;
  /** Opens the New Issue dialog in "sub-issue" mode (parent_id pre-set). */
  onCreateSubIssue: (parentId: number) => void;
  /** Called after a successful dispatch so the parent can re-fetch the issue row. */
  onIssueDispatched?: () => void;
}

type DetailTab = 'chat' | 'activity' | 'related';

export const IssueDetailView: React.FC<IssueDetailViewProps> = ({ issue, agents, agentsById, selfUserId, onCreateSubIssue, onIssueDispatched }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [tab, setTab] = useState<DetailTab>('chat');
  const [messages, setMessages] = useState<IssueMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [isAgentWorking, setIsAgentWorking] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const { addToast } = useToast();

  // Stable ref so the WS event handler always reads the latest messages
  // without needing to re-open the socket.
  const messagesRef = useRef<IssueMessage[]>([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  // ── Initial + post-action fetch ─────────────────────────────────────────
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

  // ── WebSocket: open on mount / issue.id change, close on unmount ────────
  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;

    openIssueChatSocket(issue.id, (event) => {
      if (event.type === 'chunk') {
        setStreamingText((prev) => prev + event.delta);
      } else if (event.type === 'message') {
        // Push the finalized message (dedupe by id).
        setMessages((prev) => {
          if (prev.some((m) => m.id === event.message.id)) return prev;
          return [...prev, event.message];
        });
        setStreamingText('');
        setIsAgentWorking(false);
      } else if (event.type === 'status') {
        if (event.phase === 'running') {
          setIsAgentWorking(true);
          setStreamingText('');
        } else if (event.phase === 'done') {
          setIsAgentWorking(false);
          setStreamingText('');
        }
      }
    })
      .then((socket) => {
        if (cancelled) {
          socket.close();
          return;
        }
        ws = socket;
        ws.onclose = () => {
          setStreamingText('');
          setIsAgentWorking(false);
        };
      })
      .catch((err) => {
        // Ticket acquisition failure — not fatal; initial refresh() still shows messages.
        console.debug('[IssueDetailView/WS] socket open failed:', err);
      });

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [issue.id]);

  // ── Realtime: INSERT + UPDATE on issue_messages ──────────────────────────
  // REPLICA IDENTITY FULL on issue_messages (mig 205) means the UPDATE
  // payload includes the full row, not just changed columns.
  useEffect(() => {
    const supa = getSupabaseClient();
    if (!supa) return;
    const handle = (event: 'INSERT' | 'UPDATE') => (payload: { new?: unknown; old?: unknown }) => {
      const row = (payload.new ?? payload.old) as IssueMessage | undefined;
      if (!row) return;
      setMessages((prev) => {
        if (event === 'UPDATE') {
          let touched = false;
          const next = prev.map((m) => {
            if (m.id !== row.id) return m;
            touched = true;
            return row;
          });
          return touched ? next : prev;
        }
        // INSERT: dedupe by id (POST response may have already added the row).
        if (prev.some((m) => m.id === row.id)) return prev;
        return [...prev, row];
      });
    };
    const channel = supa
      .channel(`issue-messages-${issue.id}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'issue_messages', filter: `issue_id=eq.${issue.id}` },
        handle('INSERT'),
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'issue_messages', filter: `issue_id=eq.${issue.id}` },
        handle('UPDATE'),
      )
      .subscribe();
    return () => { void supa.removeChannel(channel); };
  }, [issue.id]);

  const handleReply = async (body: string, agentId: string | null) => {
    try {
      await postIssueMessage(issue.id, { body, agent_id: agentId ?? undefined });
      // Spec-1b: for issues with an assigned agent the backend writes to
      // ai_messages (not issue_messages) and returns an optimistic comment
      // whose id does NOT match the real ai_messages row.  Appending the
      // optimistic row would leave a phantom entry that never reconciles.
      // Refetching via GET replaces the list with the canonical thread
      // (human reply + any already-completed agent reply) and naturally
      // discards the optimistic id.
      await refresh();
      addToast(agentId ? 'Reply posted; agent dispatched' : 'Comment posted', 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Send failed', 'error');
      throw err; // signal failure to keep textarea content
    }
  };

  const handleDispatch = async () => {
    if (!issue?.id) return;
    setDispatching(true);
    try {
      await dispatchIssue(issue.id);
      await refresh();
      onIssueDispatched?.();
      addToast('Agent dispatched', 'success');
    } catch (e) {
      console.error('[IssueDetailView] dispatch failed', e);
      addToast(e instanceof Error ? e.message : 'Dispatch failed', 'error');
    } finally {
      setDispatching(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800/80 text-[13px] text-zinc-500">
        <Link to={`/team/${teamId}/todolist`} className="inline-flex items-center gap-1 hover:text-zinc-300">
          <ChevronLeft size={13} />
          Issues
        </Link>
        <span className="text-zinc-600">/</span>
        <span className="text-zinc-300 truncate">{issue.title}</span>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 sm:px-8 py-6">
          <div className="flex items-center gap-2 mb-3">
            <IssueStatusIcon status={issue.status} size={15} />
            <span className="font-mono text-[12px] text-zinc-500 uppercase tracking-wider">{issue.identifier}</span>
            <span title={issue.priority}>
              <PriorityIcon priority={issue.priority} />
            </span>
            {issue.project && (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[12px]">
                <span className={`w-1.5 h-1.5 rounded-full ${issue.project.color ?? 'bg-zinc-500'}`} />
                {issue.project.name}
              </span>
            )}
            <div className="ml-auto flex items-center gap-1">
              <button className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded" title="Properties">
                <AlignLeft size={14} />
              </button>
              <button className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded" title="More">
                <MoreHorizontal size={14} />
              </button>
            </div>
          </div>

          <h1 className="text-xl font-semibold text-zinc-100 leading-tight">{issue.title}</h1>
          {issue.description && (
            <p className="mt-2 text-[14px] text-zinc-400 leading-relaxed whitespace-pre-wrap">{issue.description}</p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-5">
            <button
              onClick={() => onCreateSubIssue(issue.id)}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:bg-zinc-800/60"
            >
              <Plus size={13} /> New Sub-issue
            </button>
            <button
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-zinc-800 bg-zinc-900/50 text-zinc-500 cursor-not-allowed"
              disabled
              title="Attachments backend not in place yet"
            >
              <Paperclip size={13} /> Upload attachment
            </button>
            <button
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-zinc-800 bg-zinc-900/50 text-zinc-500 cursor-not-allowed"
              disabled
              title="Documents backend not in place yet"
            >
              <FileText size={13} /> New document
            </button>
            {issue.assignee && ['backlog', 'todo'].includes(issue.status) && (
              <button
                onClick={handleDispatch}
                disabled={dispatching}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-indigo-700/60 bg-indigo-900/30 text-indigo-300 hover:bg-indigo-800/40 disabled:opacity-50 disabled:cursor-not-allowed"
                title={`Dispatch to ${issue.assignee.name}`}
              >
                <Bot size={13} /> {dispatching ? 'Dispatching…' : 'Dispatch to Agent'}
              </button>
            )}
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
                  className={`inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium border-b-2 transition ${
                    active
                      ? 'border-indigo-400 text-zinc-100'
                      : 'border-transparent text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Icon size={13} />
                  {label}
                </button>
              );
            })}
            <span className="ml-auto text-[12px] text-zinc-600 pr-2">{messages.length} message{messages.length === 1 ? '' : 's'}</span>
          </div>

          {tab === 'chat' && (
            <>
              {error && (
                <div className="mt-4 mx-2 px-3 py-2 rounded bg-rose-500/10 text-rose-300 text-[13px] ring-1 ring-rose-500/30">
                  {error}
                </div>
              )}
              {loading && messages.length === 0
                ? <div className="text-[14px] text-zinc-500 italic px-4 py-12 text-center">Loading messages…</div>
                : <IssueChatThread messages={messages} agentsById={agentsById} selfUserId={selfUserId} streamingText={streamingText} />}
              {isAgentWorking && (
                <div className="flex items-center gap-2 px-4 py-2.5 text-[13px] text-zinc-400">
                  <span
                    className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"
                    style={{ boxShadow: '0 0 6px 1px rgba(16,185,129,0.45)' }}
                  />
                  <span className="text-zinc-400">Agent is working…</span>
                </div>
              )}
            </>
          )}
          {tab === 'activity' && (
            <IssueActivityTab messages={messages} selfUserId={selfUserId} />
          )}
          {tab === 'related' && (
            <IssueRelatedTab issue={issue} />
          )}
        </div>
      </div>

      {/* Constrain the composer to the same column width as the conversation. */}
      <div className="w-full max-w-3xl mx-auto">
        <IssueReplyBox
          agents={agents}
          defaultAgentId={issue.assignee?.id ?? null}
          onSubmit={handleReply}
        />
      </div>
    </div>
  );
};
