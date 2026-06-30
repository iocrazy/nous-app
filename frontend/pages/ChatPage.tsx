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

import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useToast } from '../components/Toast';
import { chatService } from '../services/chatService';
import { conversationService } from '../services/conversationService';
import { getResourceCoverUrl } from '../services/resourceService';
import { getTeamMembers } from '../services/teamService';
import { aiLibraryService } from '../services/aiLibraryService';
import { useChannelRealtime } from '../hooks/useChannelRealtime';
import { useConversationRealtime } from '../hooks/useConversationRealtime';
import { useChannelPresence } from '../hooks/useChannelPresence';
import { useMentionBadges } from '../hooks/useMentionBadges';
import { conversations } from '../utils/featureFlags';
import { AIChatPanel } from '../components/AIChatPanel';
import { ChatSidebar } from '../components/chat/ChatSidebar';
import { MessageList } from '../components/chat/MessageList';
import { Composer } from '../components/chat/Composer';
import { TypingIndicator } from '../components/chat/TypingIndicator';
import CreateGroupModal from '../components/chat/CreateGroupModal';
import ResourcePicker from '../components/chat/ResourcePicker';

import type { Channel, ChatMessage, ResourceItem } from '../types';

// ── Seq compare helper ────────────────────────────────────────────────────────
// seq is a Snowflake string — must use BigInt, never numeric > (precision) or
// string > (lexical). Returns false on parse error so non-numeric seqs are safe.
const seqGt = (a: string, b: string): boolean => {
  try { return BigInt(a) > BigInt(b); } catch { return false; }
};

// ── Component ─────────────────────────────────────────────────────────────────

export function ChatPage(): React.ReactElement {
  const { t } = useTranslation();
  const { selectedTeamId, currentTeam } = useTeamContext();
  const { addToast } = useToast();
  const { currentUserId, userProfile } = useAuth();

  // ── Service seam ──────────────────────────────────────────────────────────
  // conversations() is a build-time constant (VITE_FEATURE_CONVERSATIONS).
  // When OFF, this is exactly chatService — zero behaviour change.
  const svc = conversations() ? conversationService : chatService;

  // ── State ──────────────────────────────────────────────────────────────────

  const [channels, setChannels] = useState<Channel[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]); // ascending (oldest → newest)
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [sending, setSending] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showPicker, setShowPicker] = useState(false);

  /**
   * When non-null, the user has opened a DM with an agent (by slug).
   * Mutually exclusive with activeId: selecting an agent-DM sets activeId=null,
   * selecting a channel sets activeAgentDm=null.
   */
  const [activeAgentDm, setActiveAgentDm] = useState<string | null>(null);

  /** People that can be @-mentioned in the Composer (current team members, excluding self). */
  const [membersForComposer, setMembersForComposer] = useState<{ user_id: string; label: string }[]>([]);
  /** Full team-member name map (includes self) — resolves message sender_id → display name. */
  const [memberNameById, setMemberNameById] = useState<Record<string, string>>({});
  /** Chat-enabled agents that can be @-summoned in the Composer. */
  const [agentsForComposer, setAgentsForComposer] = useState<{ slug: string; label: string }[]>([]);

  /**
   * Dedupe set — tracks ids of messages already in local state.
   * Stable across renders via useRef; updated mutably (no re-render needed).
   */
  const seenIds = useRef<Set<string>>(new Set());

  /**
   * Tracks the current activeId in a ref so async callbacks can detect
   * stale loads after the user switches channels.
   */
  const activeIdRef = useRef(activeId);

  /**
   * Stable ref to messages — lets edit/delete handlers read the current list
   * without capturing a stale closure (avoids adding messages to useCallback deps).
   */
  const messagesRef = useRef<ChatMessage[]>([]);
  messagesRef.current = messages;
  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  /** In-flight guard for gapFill — prevents concurrent gap-fill runs. */
  const gapFillInFlight = useRef(false);

  // ── Mark-read debounce ────────────────────────────────────────────────────

  const markReadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleMarkRead = useCallback(
    (channelId: string, lastSeq: string) => {
      if (markReadTimer.current) clearTimeout(markReadTimer.current);
      markReadTimer.current = setTimeout(() => {
        svc.markRead(channelId, lastSeq).catch((err: unknown) => {
          // Mark-read failures are non-critical; log silently
          console.error('[ChatPage] markRead failed', err);
        });
        // Zero unread + mention badges locally (immutable update).
        // Mark-read resets mention_count server-side; mirror that locally.
        setChannels((prev) =>
          prev.map((ch) =>
            ch.id === channelId ? { ...ch, unread: 0, mentions: 0 } : ch,
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

    svc
      .listChannels()
      .then((list) => {
        if (cancelled) return;
        // Filter to only channels belonging to the currently selected team
        const filtered = list.filter(c => c.team_id === selectedTeamId);
        setChannels(filtered);
        // Auto-select first channel
        if (filtered.length > 0) {
          setActiveId(filtered[0].id);
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

  // ── Fetch member + agent lists for @-mention autocomplete ────────────────

  useEffect(() => {
    if (!selectedTeamId) {
      setMembersForComposer([]);
      setAgentsForComposer([]);
      return;
    }

    let cancelled = false;

    Promise.all([
      getTeamMembers(selectedTeamId),
      aiLibraryService.listAgents(),
    ])
      .then(([teamMembers, allAgents]) => {
        if (cancelled) return;
        // Exclude the current user from the @-people list
        setMembersForComposer(
          teamMembers
            .filter((m) => m.user_id !== currentUserId)
            .map((m) => ({ user_id: m.user_id, label: m.name ?? m.email ?? m.user_id })),
        );
        // Full map (incl. self) for resolving message sender_id → display name.
        setMemberNameById(
          Object.fromEntries(
            teamMembers.map((m) => [m.user_id, m.name ?? m.email ?? m.user_id]),
          ),
        );
        setAgentsForComposer(
          allAgents
            .filter((a) => a.chat_permissions?.enabled === true)
            .map((a) => ({ slug: a.slug, label: a.name ?? a.slug })),
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.error('[ChatPage] failed to load mention candidates', err);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedTeamId, currentUserId]);

  // ── Load messages when active channel changes ─────────────────────────────

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setHasOlder(false);
      seenIds.current.clear();
      return;
    }

    let cancelled = false;

    svc
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

    // Snapshot the channel this load is for — used to discard stale results
    // if the user switches channels before the fetch resolves.
    const loadingForId = activeId;

    // Oldest seq in current state (first element, ascending order)
    const oldestSeq = messages[0].seq;

    setLoadingOlder(true);

    svc
      .listMessages(activeId, oldestSeq, 30)
      .then((page) => {
        // Bail early if the user switched channels while this fetch was in flight
        if (activeIdRef.current !== loadingForId) return;

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

  // ── Update message in-place (for realtime UPDATEs + optimistic edit/delete) ─

  const updateMessage = useCallback((m: ChatMessage) => {
    setMessages((prev) => prev.map((x) => (x.id === m.id ? m : x)));
  }, []);

  // ── Gap-fill: forward-paginate missed messages after reconnect/focus/online ──
  //
  // Strategy: listMessages returns DESC (newest→oldest). We start from the latest
  // (cursor = undefined), collect messages with seq > lastSeq, and page backward
  // (cursor = oldestInBatch) until we reach known territory or hit the 5-page cap.
  // Finally sort ascending and push through appendMessage (dedup via seenIds).

  const gapFill = useCallback(async () => {
    const channelId = activeIdRef.current;
    if (!channelId || gapFillInFlight.current) return;
    const list = messagesRef.current; // ascending (oldest → newest)
    const lastSeq = list.length ? list[list.length - 1].seq : '0';
    gapFillInFlight.current = true;
    try {
      let cursor: string | undefined = undefined; // start from latest
      const fresh: ChatMessage[] = [];
      for (let page = 0; page < 5; page++) {
        const batch = await svc.listMessages(channelId, cursor, 30); // DESC
        if (channelId !== activeIdRef.current) return; // channel switched mid-fetch
        if (!batch.length) break;
        // batch is newest→oldest; collect those strictly newer than lastSeq
        const newer = batch.filter((m) => seqGt(m.seq, lastSeq));
        fresh.push(...newer);
        const oldestInBatch = batch[batch.length - 1].seq;
        if (!seqGt(oldestInBatch, lastSeq)) break; // reached known territory
        cursor = oldestInBatch; // page further back toward lastSeq
        if (page === 4 && seqGt(oldestInBatch, lastSeq)) {
          console.warn('[chat] gapFill: gap exceeds 150 messages; reload to see the rest');
        }
      }
      if (fresh.length) {
        // Sort ascending so appendMessage inserts in the right order
        fresh.sort((a, b) => (seqGt(a.seq, b.seq) ? 1 : -1));
        for (const m of fresh) appendMessage(m);
        const maxSeq = fresh[fresh.length - 1].seq;
        scheduleMarkRead(channelId, maxSeq);
      }
    } catch (err) {
      console.error('[chat] gapFill failed', err);
    } finally {
      gapFillInFlight.current = false;
    }
  }, [appendMessage, scheduleMarkRead]);

  // ── Wire online + visibility events to gapFill ────────────────────────────

  useEffect(() => {
    const onOnline = () => { void gapFill(); };
    const onVisible = () => { if (document.visibilityState === 'visible') void gapFill(); };
    window.addEventListener('online', onOnline);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('online', onOnline);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [gapFill]);

  // ── Edit/delete handlers ──────────────────────────────────────────────────

  const handleEditMessage = useCallback(
    async (messageId: string, text: string) => {
      if (!activeIdRef.current) return;
      // Merge with original body so non-text keys (e.g. sender_name) are preserved
      const original = messagesRef.current.find((x) => x.id === messageId);
      const originalBody = original?.body ?? {};
      try {
        const updated = await svc.editMessage(
          activeIdRef.current,
          messageId,
          { ...originalBody, text },
        );
        updateMessage(updated);
      } catch (err) {
        console.error(err);
        addToast(t('chat.editError'), 'error');
      }
    },
    [updateMessage, addToast, t],
  );

  const handleDeleteMessage = useCallback(
    async (messageId: string) => {
      if (!activeIdRef.current) return;
      try {
        const updated = await svc.deleteMessage(
          activeIdRef.current,
          messageId,
        );
        updateMessage(updated);
      } catch (err) {
        console.error(err);
        addToast(t('chat.deleteError'), 'error');
      }
    },
    [updateMessage, addToast, t],
  );

  // ── Realtime subscription ─────────────────────────────────────────────────
  //
  // Rules-of-hooks seam: both hooks are always called; the inactive one receives
  // null so its useEffect no-ops (both hooks null-guard at the top of the effect).
  // conversations() is a build-time constant, so the routing is stable across
  // renders and there is no conditional hook invocation.

  const _legacyId = conversations() ? null : activeId;
  const _convId   = conversations() ? activeId : null;

  const _onRealtimeInsert = (m: ChatMessage) => {
    appendMessage(m);
    if (activeId) scheduleMarkRead(activeId, m.seq);
  };

  useChannelRealtime(_legacyId, _onRealtimeInsert, updateMessage, gapFill);
  useConversationRealtime(_convId, _onRealtimeInsert, updateMessage, gapFill);

  // ── Presence + typing ─────────────────────────────────────────────────────

  const me = currentUserId
    ? { user_id: currentUserId, name: userProfile.name ?? currentUserId }
    : null;

  const { onlineUserIds, typingUsers, sendTyping } = useChannelPresence(activeId, me);

  // ── Live mention badge updates ─────────────────────────────────────────────

  useMentionBadges(currentUserId, (channelId, mentionCount) => {
    setChannels((prev) =>
      prev.map((ch) =>
        ch.id === channelId ? { ...ch, mentions: mentionCount } : ch,
      ),
    );
  });

  // ── Send message ──────────────────────────────────────────────────────────

  const handleSend = useCallback(
    (text: string, mentionUserIds: string[]) => {
      if (!activeId || sending) return;

      setSending(true);

      const body: Record<string, unknown> = { text };
      if (mentionUserIds.length > 0) {
        body.mention_user_ids = mentionUserIds;
      }

      svc
        .sendMessage(activeId, body)
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

  // ── Send media card ───────────────────────────────────────────────────────

  const handleSendMedia = useCallback(
    (item: ResourceItem) => {
      if (!activeId) return;

      const r = item.resource;
      const isImage = r?.mime_type?.startsWith('image/') ?? false;
      const imageUrl =
        r?.id && (r.thumbnail_path || r.cover_image_path || r.media_id || isImage)
          ? getResourceCoverUrl(String(r.id))
          : undefined;

      const sizeValue = (() => {
        const bytes = r?.file_size_bytes;
        if (!bytes) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
        return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
      })();

      const body: Record<string, unknown> = {
        resource_id: String(item.resource_id),
        title: r?.filename ?? t('chat.mediaCard.untitled'),
        image_url: imageUrl,
        fields: [
          { title: t('chat.mediaCard.type'), value: r?.mime_type ?? '' },
          { title: t('chat.mediaCard.size'), value: sizeValue },
        ].filter((f) => f.value),
      };

      setSending(true);

      svc
        .sendMessage(activeId, body, 'media_card')
        .then((sent) => {
          appendMessage(sent);
        })
        .catch((err: unknown) => {
          console.error('[ChatPage] sendMedia failed', err);
          addToast(t('chat.mediaCard.sendError'), 'error');
        })
        .finally(() => {
          setSending(false);
        });
    },
    [activeId, appendMessage, addToast, t],
  );

  // ── Channel selection ─────────────────────────────────────────────────────

  const handleSelectChannel = useCallback((id: string) => {
    setActiveId(id);
    setActiveAgentDm(null);
  }, []);

  // ── Guard: no team selected ───────────────────────────────────────────────

  if (!selectedTeamId || !currentTeam) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-ink-500">
        <p className="text-lg font-medium text-ink-400">{t('chat.noTeam')}</p>
        <p className="text-sm mt-1">{t('chat.selectTeam')}</p>
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
          onNew={() => setShowCreate(true)}
          agents={agentsForComposer}
          activeAgentDm={activeAgentDm}
          onSelectAgentDm={(slug) => {
            setActiveAgentDm(slug);
            setActiveId(null);
          }}
        />
      </div>

      {/* ── Conversation island ── */}
      <div className="flex-1 flex flex-col h-full bg-island border border-line-strong rounded-[18px] overflow-hidden min-w-0">
        {activeAgentDm !== null ? (
          /* ── Agent DM view: render AIChatPanel locked to the selected agent ── */
          <AIChatPanel
            key={activeAgentDm}
            agentSlug={activeAgentDm}
            onClose={() => setActiveAgentDm(null)}
          />
        ) : (
          /* ── Channel view ── */
          <>
            {/* Header */}
            <div className="flex items-center gap-[10px] px-[18px] py-[13px] border-b border-line flex-shrink-0">
              <MessageSquare size={15} className="text-content-3 flex-shrink-0" />
              <span className="text-[15px] font-[650] tracking-[-0.01em] text-content whitespace-nowrap overflow-hidden text-ellipsis">
                {activeChannel?.name ?? activeChannel?.id ?? t('chat.noChannels')}
              </span>
              {activeChannel?.topic && (
                <>
                  <span className="text-content-4 text-[13px]">·</span>
                  <span className="text-[12.5px] text-content-3 whitespace-nowrap overflow-hidden text-ellipsis">
                    {activeChannel.topic}
                  </span>
                </>
              )}
              {activeChannel && onlineUserIds.length > 0 && (
                <div className="ml-auto flex items-center gap-[5px] flex-shrink-0">
                  <span className="w-[6px] h-[6px] rounded-full bg-emerald-400 flex-shrink-0" />
                  <span className="text-[11.5px] text-content-3">
                    {t('chat.typing.online', { count: onlineUserIds.length })}
                  </span>
                </div>
              )}
            </div>

            {/* Body: message list or empty state */}
            {channels.length === 0 || !activeId ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
                <MessageSquare size={36} className="text-content-4" />
                <p className="text-[14px] text-content-3">{t('chat.noChannels')}</p>
              </div>
            ) : (
              <>
                <MessageList
                  messages={messages}
                  onLoadOlder={handleLoadOlder}
                  hasOlder={hasOlder}
                  loadingOlder={loadingOlder}
                  currentUserId={currentUserId}
                  memberNameById={memberNameById}
                  onEdit={handleEditMessage}
                  onDelete={handleDeleteMessage}
                />
                <TypingIndicator names={typingUsers.map((u) => u.name)} />
                <Composer
                  onSend={handleSend}
                  onAttachMedia={() => setShowPicker(true)}
                  onTyping={sendTyping}
                  disabled={sending || !activeId}
                  placeholder={
                    activeChannel
                      ? t('chat.composerPlaceholder')
                      : undefined
                  }
                  members={membersForComposer}
                  agents={agentsForComposer}
                />
              </>
            )}
          </>
        )}
      </div>
      {selectedTeamId && (
        <CreateGroupModal
          teamId={selectedTeamId}
          open={showCreate}
          onClose={() => setShowCreate(false)}
          onCreated={(ch) => {
            setShowCreate(false);
            setChannels((prev) => [ch, ...prev]);
            setActiveId(ch.id);
            setActiveAgentDm(null);
          }}
        />
      )}
      {activeId && selectedTeamId && (
        <ResourcePicker
          open={showPicker}
          teamId={selectedTeamId}
          onClose={() => setShowPicker(false)}
          onSelect={handleSendMedia}
        />
      )}
    </div>
  );
}
