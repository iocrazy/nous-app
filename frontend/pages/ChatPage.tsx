/**
 * ChatPage — Team Chat container.
 *
 * Wires together:
 *   - chatService  (Task 1): listChannels / listMessages / sendMessage / markRead
 *   - Chat presentational components (Task 2): ChatSidebar, MessageList, Composer
 *   - useChannelRealtime (Task 3): Supabase realtime INSERT subscription
 *
 * State lives entirely here; child components are purely presentational.
 *
 * Dedupe strategy: a Set<string> of seen message ids prevents the sender
 * seeing their own message twice (sendMessage append + realtime echo).
 *
 * Mark-read: debounced 800 ms after messages load or a new message arrives
 * while the channel is active; also zeroes the channel's unread badge locally.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MessageSquare } from 'lucide-react';

import { useTeamContext } from '../contexts/TeamContext';
import { useToast } from '../components/Toast';
import { chatService } from '../services/chatService';
import { useChannelRealtime } from '../hooks/useChannelRealtime';
import { ChatSidebar } from '../components/chat/ChatSidebar';
import { MessageList } from '../components/chat/MessageList';
import { Composer } from '../components/chat/Composer';

import type { Channel, ChatMessage } from '../types';

// ── helpers ──────────────────────────────────────────────────────────────────

/** Snowflake seq comparison — convert to Number only for ordering. */
function minSeq(a: string, b: string): string {
  return Number(a) < Number(b) ? a : b;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ChatPage(): React.ReactElement {
  const { t } = useTranslation();
  const { selectedTeamId, currentTeam } = useTeamContext();
  const { addToast } = useToast();

  // ── State ──────────────────────────────────────────────────────────────────

  const [channels, setChannels] = useState<Channel[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]); // ascending (oldest → newest)
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [sending, setSending] = useState(false);

  /**
   * Dedupe set — tracks ids of messages already in local state.
   * Stable across renders via useRef; updated mutably (no re-render needed).
   */
  const seenIds = useRef<Set<string>>(new Set());

  // ── Mark-read debounce ────────────────────────────────────────────────────

  const markReadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleMarkRead = useCallback(
    (channelId: string, lastSeq: string) => {
      if (markReadTimer.current) clearTimeout(markReadTimer.current);
      markReadTimer.current = setTimeout(() => {
        chatService.markRead(channelId, lastSeq).catch((err: unknown) => {
          // Mark-read failures are non-critical; log silently
          console.error('[ChatPage] markRead failed', err);
        });
        // Zero unread badge locally (immutable update)
        setChannels((prev) =>
          prev.map((ch) =>
            ch.id === channelId ? { ...ch, unread: 0 } : ch,
          ),
        );
      }, 800);
    },
    [],
  );

  // Clear debounce on unmount
  useEffect(() => {
    return () => {
      if (markReadTimer.current) clearTimeout(markReadTimer.current);
    };
  }, []);

  // ── Load channels on mount / team change ──────────────────────────────────

  useEffect(() => {
    if (!selectedTeamId) return;

    let cancelled = false;

    chatService
      .listChannels()
      .then((list) => {
        if (cancelled) return;
        setChannels(list);
        // Auto-select first channel
        if (list.length > 0) {
          setActiveId(list[0].id);
        } else {
          setActiveId(null);
          setMessages([]);
          seenIds.current.clear();
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('chat.errorLoadChannels', { error: msg }), 'error');
      });

    return () => {
      cancelled = true;
    };
  }, [selectedTeamId, addToast, t]);

  // ── Load messages when active channel changes ─────────────────────────────

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setHasOlder(false);
      seenIds.current.clear();
      return;
    }

    let cancelled = false;

    chatService
      .listMessages(activeId, undefined, 30)
      .then((page) => {
        if (cancelled) return;

        // API returns newest-first (DESC); reverse to ascending for display
        const ascending = [...page].reverse();

        seenIds.current = new Set(ascending.map((m) => m.id));
        setMessages(ascending);
        setHasOlder(page.length === 30);

        // Schedule mark-read if there are messages
        if (ascending.length > 0) {
          const lastSeq = ascending[ascending.length - 1].seq;
          scheduleMarkRead(activeId, lastSeq);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('chat.errorLoadMessages', { error: msg }), 'error');
      });

    return () => {
      cancelled = true;
    };
  }, [activeId, addToast, t, scheduleMarkRead]);

  // ── Load older messages ───────────────────────────────────────────────────

  const handleLoadOlder = useCallback(() => {
    if (!activeId || loadingOlder || messages.length === 0) return;

    // Oldest seq in current state (first element, ascending order)
    const oldestSeq = messages[0].seq;

    setLoadingOlder(true);

    chatService
      .listMessages(activeId, oldestSeq, 30)
      .then((page) => {
        // API returns newest-first; reverse to ascending then prepend
        const ascending = [...page].reverse();

        // Dedupe against seenIds before prepending
        const fresh = ascending.filter((m) => {
          if (seenIds.current.has(m.id)) return false;
          seenIds.current.add(m.id);
          return true;
        });

        setMessages((prev) => [...fresh, ...prev]);
        setHasOlder(page.length === 30);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('chat.errorLoadMessages', { error: msg }), 'error');
      })
      .finally(() => {
        setLoadingOlder(false);
      });
  }, [activeId, loadingOlder, messages, addToast, t]);

  // ── Append message helper (dedupe by id) ──────────────────────────────────

  const appendMessage = useCallback((m: ChatMessage) => {
    if (seenIds.current.has(m.id)) return;
    seenIds.current.add(m.id);
    setMessages((prev) => [...prev, m]);
  }, []);

  // ── Realtime subscription ─────────────────────────────────────────────────

  useChannelRealtime(activeId, (m) => {
    appendMessage(m);

    // Schedule mark-read when a new realtime message arrives
    if (activeId) {
      scheduleMarkRead(activeId, m.seq);
    }
  });

  // ── Send message ──────────────────────────────────────────────────────────

  const handleSend = useCallback(
    (text: string) => {
      if (!activeId || sending) return;

      setSending(true);

      chatService
        .sendMessage(activeId, { text })
        .then((sent) => {
          appendMessage(sent);
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
          addToast(t('chat.errorSend', { error: msg }), 'error');
        })
        .finally(() => {
          setSending(false);
        });
    },
    [activeId, sending, appendMessage, addToast, t],
  );

  // ── Channel selection ─────────────────────────────────────────────────────

  const handleSelectChannel = useCallback((id: string) => {
    setActiveId(id);
  }, []);

  // ── Guard: no team selected ───────────────────────────────────────────────

  if (!selectedTeamId || !currentTeam) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-ink-500">
        <p className="text-lg font-medium text-ink-400">No team selected</p>
        <p className="text-sm mt-1">Select a team to use Team Chat</p>
      </div>
    );
  }

  // ── Active channel meta ───────────────────────────────────────────────────

  const activeChannel = channels.find((c) => c.id === activeId) ?? null;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full gap-3 p-3 overflow-hidden">
      {/* ── Sidebar island ── */}
      <div className="w-[220px] flex-shrink-0 h-full">
        <ChatSidebar
          channels={channels}
          activeId={activeId ?? ''}
          onSelect={handleSelectChannel}
        />
      </div>

      {/* ── Conversation island ── */}
      <div className="flex-1 flex flex-col h-full bg-[#15151a] border border-white/[.12] rounded-[18px] overflow-hidden min-w-0">
        {/* Header */}
        <div className="flex items-center gap-[10px] px-[18px] py-[13px] border-b border-white/[.08] flex-shrink-0">
          <MessageSquare size={15} className="text-[#74747e] flex-shrink-0" />
          <span className="text-[15px] font-[650] tracking-[-0.01em] text-[#e7e7ea] whitespace-nowrap overflow-hidden text-ellipsis">
            {activeChannel?.name ?? activeChannel?.id ?? t('chat.noChannels')}
          </span>
          {activeChannel?.topic && (
            <>
              <span className="text-[#4a4a52] text-[13px]">·</span>
              <span className="text-[12.5px] text-[#74747e] whitespace-nowrap overflow-hidden text-ellipsis">
                {activeChannel.topic}
              </span>
            </>
          )}
        </div>

        {/* Body: message list or empty state */}
        {channels.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
            <MessageSquare size={36} className="text-[#4a4a52]" />
            <p className="text-[14px] text-[#74747e]">{t('chat.noChannels')}</p>
          </div>
        ) : (
          <>
            <MessageList
              messages={messages}
              onLoadOlder={handleLoadOlder}
              hasOlder={hasOlder}
              loadingOlder={loadingOlder}
            />
            <Composer
              onSend={handleSend}
              disabled={sending || !activeId}
              placeholder={
                activeChannel
                  ? t('chat.composerPlaceholder')
                  : undefined
              }
            />
          </>
        )}
      </div>
    </div>
  );
}
