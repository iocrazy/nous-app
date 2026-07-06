// frontend/pages/SessionsPage.tsx
// AI Library → Sessions: cross-agent chat-session management.
//
// Master-detail: left column lists every session the caller owns (newest
// first, server-side title search), right pane shows the selected
// session's full transcript (read-only) with rename / delete actions.
// Reuses MessageBubble so attachments/images render exactly like the
// floating chat panel.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Check, MessageSquare, MessagesSquare, Pencil, Search, Trash2, X } from 'lucide-react';

import type { AIChatMessage, ChatSession } from '../types';
import { aiLibraryService } from '../services/aiLibraryService';
import { MessageBubble } from '../components/chat/AIChatBubble';
import { useToast } from '../components/Toast';
import { PageHeader } from '../components/AILibrary/PageHeader';
import { useGlobalChatStore } from '../stores/globalChatStore';

function formatWhen(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export const SessionsPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId, sessionId } = useParams();
  const { addToast } = useToast();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  const base = `${urlPrefix}/ai-library/sessions`;

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [messages, setMessages] = useState<AIChatMessage[] | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [agentFilter, setAgentFilter] = useState<string | null>(null);
  const requestChat = useGlobalChatStore((s) => s.requestChat);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const selected = useMemo(
    () => sessions.find((s) => s.id === sessionId) ?? null,
    [sessions, sessionId],
  );

  // Agent filter chips: derived client-side (list is capped at 100 rows),
  // ordered by session count so the busiest agents come first.
  const agentChips = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of sessions) {
      if (s.agent_slug) counts.set(s.agent_slug, (counts.get(s.agent_slug) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [sessions]);

  const visibleSessions = useMemo(
    () => (agentFilter ? sessions.filter((s) => s.agent_slug === agentFilter) : sessions),
    [sessions, agentFilter],
  );

  const loadSessions = useCallback(async (q: string) => {
    setListLoading(true);
    try {
      const list = await aiLibraryService.listAllChatSessions(q || undefined, 100);
      setSessions(list);
    } catch (err) {
      console.error('[SessionsPage] listAllChatSessions failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
      setListLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    void loadSessions('');
  }, [loadSessions]);

  // Debounced server-side search.
  const handleSearch = (value: string) => {
    setSearch(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => void loadSessions(value.trim()), 300);
  };

  // Load transcript when the URL selects a session.
  useEffect(() => {
    if (!sessionId) {
      setMessages(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    void (async () => {
      try {
        const data = await aiLibraryService.getChatSession(sessionId);
        if (!cancelled) setMessages(data.messages ?? []);
      } catch (err) {
        if (!cancelled) {
          console.error('[SessionsPage] getChatSession failed:', err);
          setMessages([]);
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const startRename = (s: ChatSession) => {
    setRenamingId(s.id);
    setRenameDraft(s.title ?? '');
  };

  const commitRename = async () => {
    if (!renamingId) return;
    const title = renameDraft.trim();
    if (!title) {
      setRenamingId(null);
      return;
    }
    try {
      const updated = await aiLibraryService.updateChatSession(renamingId, { title });
      setSessions((prev) =>
        prev.map((s) => (s.id === renamingId ? { ...s, title: updated.title ?? title } : s)),
      );
    } catch (err) {
      console.error('[SessionsPage] rename failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
      setRenamingId(null);
    }
  };

  const handleDelete = async (s: ChatSession) => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(t('aiLibrary.sessions.deleteConfirm', 'Delete this session and its messages?'))) {
      return;
    }
    try {
      await aiLibraryService.deleteChatSession(s.id);
      setSessions((prev) => prev.filter((x) => x.id !== s.id));
      if (sessionId === s.id) navigate(base);
    } catch (err) {
      console.error('[SessionsPage] delete failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  return (
    <div className="flex h-full min-h-0 gap-4 pt-6">
      {/* ── Session list ── */}
      <div className="flex w-80 flex-shrink-0 flex-col min-h-0">
        <PageHeader
          title={t('aiLibrary.sessions.title', 'Sessions')}
          count={sessions.length}
          className="pb-3"
        />
        <div className="relative mb-2">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-600" />
          <input
            type="text"
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={t('aiLibrary.sessions.searchPlaceholder', 'Search sessions...')}
            className="w-full rounded-md border border-ink-800 bg-ink-900/40 py-1.5 pl-8 pr-3 text-[13px] text-ink-200 placeholder-ink-600 focus:border-indigo-500/50 focus:outline-none"
          />
        </div>
        {agentChips.length > 1 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {agentChips.map(([slug, count]) => {
              const on = agentFilter === slug;
              return (
                <button
                  key={slug}
                  type="button"
                  onClick={() => setAgentFilter(on ? null : slug)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                    on
                      ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-300'
                      : 'border-ink-800 text-ink-500 hover:text-ink-300 hover:border-ink-700'
                  }`}
                >
                  {slug} <span className="opacity-60">{count}</span>
                </button>
              );
            })}
          </div>
        )}
        <div className="flex-1 space-y-1 overflow-y-auto pr-1 min-h-0">
          {listLoading ? (
            <div className="px-2 py-4 text-sm text-ink-500">{t('common.loading')}</div>
          ) : visibleSessions.length === 0 ? (
            <div className="px-2 py-4 text-sm text-ink-500">
              {t('aiLibrary.sessions.empty', 'No sessions yet — start one from the chat panel.')}
            </div>
          ) : (
            visibleSessions.map((s) => {
              const active = s.id === sessionId;
              return (
                <div
                  key={s.id}
                  className={`group rounded-lg border px-3 py-2 transition-colors cursor-pointer ${
                    active
                      ? 'border-indigo-500/40 bg-indigo-500/8'
                      : 'border-transparent hover:border-ink-800 hover:bg-ink-900/40'
                  }`}
                  onClick={() => navigate(`${base}/${s.id}`)}
                >
                  {renamingId === s.id ? (
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <input
                        autoFocus
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') void commitRename();
                          if (e.key === 'Escape') setRenamingId(null);
                        }}
                        className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-1.5 py-0.5 text-[13px] text-ink-200 focus:outline-none"
                      />
                      <button type="button" onClick={() => void commitRename()} className="text-emerald-400 hover:text-emerald-300">
                        <Check size={13} />
                      </button>
                      <button type="button" onClick={() => setRenamingId(null)} className="text-ink-500 hover:text-ink-300">
                        <X size={13} />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-200">
                        {s.title || t('aiLibrary.sessions.untitled', 'Untitled')}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          startRename(s);
                        }}
                        className="hidden text-ink-600 hover:text-ink-300 group-hover:block"
                        title={t('common.rename', 'Rename')}
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDelete(s);
                        }}
                        className="hidden text-ink-600 hover:text-red-400 group-hover:block"
                        title={t('common.delete', 'Delete')}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  )}
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-ink-600">
                    {s.agent_slug && (
                      <span className="rounded bg-ink-800/80 px-1.5 py-0.5 text-ink-400">
                        {s.agent_slug}
                      </span>
                    )}
                    {s.message_count != null && (
                      <span>{t('aiLibrary.sessions.msgCount', '{{count}} msgs', { count: s.message_count })}</span>
                    )}
                    <span className="ml-auto">{formatWhen(s.updated_at ?? s.created_at)}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Transcript pane ── */}
      <div className="flex min-w-0 flex-1 flex-col rounded-xl border border-ink-800/60 bg-ink-900/20 min-h-0">
        {!sessionId ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-600">
            <MessageSquare size={28} />
            <span className="text-sm">
              {t('aiLibrary.sessions.pickOne', 'Select a session to view its transcript.')}
            </span>
          </div>
        ) : detailLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-500">
            {t('common.loading')}
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-ink-800/60 px-4 py-3">
              <span className="truncate text-sm font-medium text-ink-200">
                {selected?.title || t('aiLibrary.sessions.untitled', 'Untitled')}
              </span>
              {selected?.agent_slug && (
                <span className="rounded bg-ink-800/80 px-1.5 py-0.5 text-[11px] text-ink-400">
                  {selected.agent_slug}
                </span>
              )}
              <div className="ml-auto flex items-center gap-3">
                {selected?.total_tokens != null && (
                  <span className="text-[11px] text-ink-600">
                    {selected.total_tokens} tokens
                  </span>
                )}
                {selected?.agent_slug && (
                  <button
                    type="button"
                    onClick={() => requestChat(selected.agent_slug!, selected.id)}
                    className="flex items-center gap-1.5 rounded-md border border-indigo-500/40 bg-indigo-500/10 px-2.5 py-1 text-[12px] text-indigo-300 transition-colors hover:bg-indigo-500/20"
                  >
                    <MessagesSquare size={13} />
                    {t('aiLibrary.sessions.openInChat', 'Open in Chat')}
                  </button>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-4 min-h-0">
              {(messages ?? []).length === 0 ? (
                <div className="py-8 text-center text-sm text-ink-600">
                  {t('aiLibrary.sessions.noMessages', 'No messages in this session.')}
                </div>
              ) : (
                (messages ?? []).map((m) => (
                  <MessageBubble
                    key={m.id}
                    role={m.role === 'system' ? 'assistant' : m.role}
                    content={m.content}
                    attachments={m.attachments ?? undefined}
                    tokens={
                      m.prompt_tokens != null && m.completion_tokens != null
                        ? (m.prompt_tokens ?? 0) + (m.completion_tokens ?? 0)
                        : undefined
                    }
                    timestamp={formatWhen(m.created_at)}
                  />
                ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default SessionsPage;
