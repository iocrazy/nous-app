/**
 * AIChatPanel — right-rail chat panel used by ScriptEditor + StoryboardWorkbench.
 *
 * As of U2 this talks to the unified AI Library framework:
 *   - Agents come from ``/api/v1/ai-library/agents`` (the same list the
 *     sidebar AI LIBRARY section renders).
 *   - Sessions + messages flow through ``/api/v1/ai-library/sessions``
 *     and every chat turn produces an agent_runs row, so the pulse /
 *     Runs tab / Usage dashboard / budget guard all work for chat too.
 *
 * The U1 backend does NOT stream yet — responses are delivered as a
 * single POST. We show a typing indicator during the in-flight request
 * instead of character-by-character streaming. Streaming SSE can be
 * added in a follow-up if the perceived latency is an issue.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, X } from 'lucide-react';

import { aiLibraryService } from '../services/aiLibraryService';
import type {
  AILibraryAgent,
  ChatMessage,
  ChatSession,
  ChatToolCall,
} from '../types';
import { AgentSelector } from './AgentSelector';
import { SessionList, type SessionItem } from './SessionList';
import { MessageBubble } from './chat/MessageBubble';
import { TypingIndicator } from './chat/TypingIndicator';
import { ChatInput } from './chat/ChatInput';
import { EmptyState } from './chat/EmptyState';
import { useToast } from './Toast';

export interface AIChatPanelProps {
  /** String form of the project's BIGINT id, for display + session tagging. */
  projectId: string;
  contextType?: 'script' | 'storyboard';
  contextId?: string;
  onApplyContent?: (content: string) => void;
  onClose?: () => void;
}

function formatTimestamp(isoString?: string | null): string {
  if (!isoString) return '';
  return new Date(isoString).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Extract the per-turn Skill / Delegate trace from a persisted assistant
 * message. Backend folds it into ``metadata_json.tool_calls``; older
 * rows (pre Step B) just won't have the field. Returns [] for any shape
 * we don't recognise so the renderer can drop in unconditionally.
 */
function extractToolCalls(msg: ChatMessage): ChatToolCall[] {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return [];
  const raw = (meta as Record<string, unknown>).tool_calls;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (entry): entry is ChatToolCall =>
      typeof entry === 'object' &&
      entry !== null &&
      typeof (entry as ChatToolCall).name === 'string',
  );
}

/** Coerce the string project id to a BIGINT-compatible number when possible. */
function parseProjectId(projectId: string): number | undefined {
  const n = Number(projectId);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

export function AIChatPanel({
  projectId,
  contextType,
  contextId,
  onApplyContent,
  onClose,
}: AIChatPanelProps): React.ReactElement {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  const numericProjectId = useMemo(() => parseProjectId(projectId), [projectId]);

  // Load the agent list for the selector. Sessions are loaded lazily per
  // agent selection so switching agents doesn't drag in noise from others.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (cancelled) return;
        // Only enabled agents are selectable; the sidebar already surfaces
        // disabled ones visually, but chat requires an executable agent.
        const enabled = list.filter((a) => a.enabled);
        setAgents(enabled);
        if (enabled.length > 0) {
          setSelectedAgentSlug((prev) => prev ?? enabled[0].slug);
        }
      } catch (err) {
        console.error('[AIChatPanel] listAgents failed:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // When the selected agent changes, (re)load that agent's sessions for
  // this project. If there are none, auto-create one so the input isn't
  // permanently disabled on first open.
  useEffect(() => {
    if (!selectedAgentSlug) return;
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listChatSessions(
          selectedAgentSlug,
          numericProjectId,
        );
        if (cancelled) return;
        setSessions(list);
        if (list.length > 0) {
          setActiveSessionId(list[0].id);
          await loadSessionMessages(list[0].id, () => cancelled);
        } else {
          const created = await aiLibraryService.createChatSession(
            selectedAgentSlug,
            {
              title: t('chat.newConversation', 'New conversation'),
              project_id: numericProjectId,
              context_type: contextType,
              context_id: contextId,
            },
          );
          if (cancelled) return;
          setSessions([created]);
          setActiveSessionId(created.id);
          setMessages([]);
        }
      } catch (err) {
        console.error('[AIChatPanel] session load failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentSlug, numericProjectId]);

  async function loadSessionMessages(
    sessionId: string,
    isCancelled: () => boolean = () => false,
  ): Promise<void> {
    try {
      const data = await aiLibraryService.getChatSession(sessionId);
      if (isCancelled()) return;
      setMessages(data.messages ?? []);
    } catch (err) {
      console.error('[AIChatPanel] getChatSession failed:', err);
    }
  }

  const handleSelectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setMessages([]);
    await loadSessionMessages(sessionId);
  }, []);

  const handleNewSession = useCallback(async () => {
    if (!selectedAgentSlug) return;
    try {
      const created = await aiLibraryService.createChatSession(
        selectedAgentSlug,
        {
          title: t('chat.newConversation', 'New conversation'),
          project_id: numericProjectId,
          context_type: contextType,
          context_id: contextId,
        },
      );
      setSessions((prev) => [created, ...prev]);
      setActiveSessionId(created.id);
      setMessages([]);
    } catch (err) {
      console.error('[AIChatPanel] createChatSession failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      addToast(`Failed to create session: ${msg}`, 'error');
    }
  }, [selectedAgentSlug, numericProjectId, contextType, contextId, t, addToast]);

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await aiLibraryService.deleteChatSession(sessionId);
        const next = sessions.filter((s) => s.id !== sessionId);
        setSessions(next);
        if (activeSessionId === sessionId) {
          if (next.length > 0) {
            setActiveSessionId(next[0].id);
            await loadSessionMessages(next[0].id);
          } else {
            setActiveSessionId(null);
            setMessages([]);
          }
        }
      } catch (err) {
        console.error('[AIChatPanel] deleteChatSession failed:', err);
      }
    },
    [sessions, activeSessionId],
  );

  const handleSend = useCallback(
    async (text: string) => {
      if (!activeSessionId || sending) return;
      setSending(true);

      // Optimistic user bubble — replaced by the authoritative row after
      // the server responds and we reload the message list.
      const tempUser: ChatMessage = {
        id: `tmp-user-${Date.now()}`,
        session_id: activeSessionId,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempUser]);

      try {
        await aiLibraryService.sendChatMessage(activeSessionId, text);
        // Refetch full history so IDs + timestamps are server-authoritative.
        await loadSessionMessages(activeSessionId);
      } catch (err) {
        console.error('[AIChatPanel] sendChatMessage failed:', err);
        const msg = err instanceof Error ? err.message : String(err);
        addToast(`Send failed: ${msg}`, 'error');
        // Roll back the optimistic bubble — the server didn't accept it.
        setMessages((prev) => prev.filter((m) => m.id !== tempUser.id));
      } finally {
        setSending(false);
      }

      // Update the session list ordering so this session bubbles to top.
      if (selectedAgentSlug) {
        void aiLibraryService
          .listChatSessions(selectedAgentSlug, numericProjectId)
          .then(setSessions)
          .catch((err) => console.error('[AIChatPanel] refresh sessions failed:', err));
      }
    },
    [activeSessionId, sending, selectedAgentSlug, numericProjectId, addToast],
  );

  const handleSuggest = useCallback(
    (suggestion: string) => {
      void handleSend(suggestion);
    },
    [handleSend],
  );

  const sessionItems: SessionItem[] = useMemo(
    () =>
      (sessions || []).filter(Boolean).map((s) => ({
        id: s.id,
        title: s.title ?? t('chat.untitled', 'Untitled'),
        message_count: s.message_count ?? 0,
        updated_at: s.updated_at ?? '',
      })),
    [sessions, t],
  );

  // AgentOption wants {id, name, description?}. We thread agent.slug as
  // id because selection downstream uses slug — it's the session FK.
  const agentOptions = useMemo(
    () =>
      agents.map((a) => ({
        id: a.slug,
        name: a.name,
        description: a.description ?? undefined,
      })),
    [agents],
  );

  const hasMessages = messages.length > 0 || sending;

  return (
    <div className="w-full h-full flex-1 flex flex-col bg-zinc-900 overflow-hidden min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 flex-shrink-0">
        <span className="text-sm font-medium text-zinc-200 flex-1">AI Chat</span>

        <AgentSelector
          agents={agentOptions}
          selectedId={selectedAgentSlug}
          onSelect={setSelectedAgentSlug}
        />

        <button
          type="button"
          onClick={handleNewSession}
          className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
          title={t('chat.newSession', 'New session')}
        >
          <Plus size={15} />
        </button>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
            title={t('common.close', 'Close')}
          >
            <X size={15} />
          </button>
        )}
      </div>

      {/* Session list */}
      <SessionList
        sessions={sessionItems}
        activeSessionId={activeSessionId}
        onSelect={handleSelectSession}
        onNew={handleNewSession}
        onDelete={handleDeleteSession}
      />

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-3 py-3 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <TypingIndicator />
          </div>
        ) : !hasMessages ? (
          <EmptyState onSuggest={handleSuggest} />
        ) : (
          <>
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                role={msg.role === 'system' ? 'assistant' : msg.role}
                content={msg.content}
                tokens={
                  msg.prompt_tokens != null && msg.completion_tokens != null
                    ? (msg.prompt_tokens ?? 0) + (msg.completion_tokens ?? 0)
                    : undefined
                }
                timestamp={formatTimestamp(msg.created_at)}
                toolCalls={
                  msg.role === 'assistant' ? extractToolCalls(msg) : undefined
                }
                onApply={
                  msg.role === 'assistant' && onApplyContent
                    ? () => onApplyContent(msg.content)
                    : undefined
                }
              />
            ))}

            {sending && (
              <div className="flex justify-start mb-3">
                <div className="max-w-[85%] rounded-xl bg-zinc-800 text-zinc-200 text-sm leading-relaxed overflow-hidden">
                  <TypingIndicator />
                </div>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Chat input */}
      <ChatInput
        onSend={handleSend}
        disabled={sending || !activeSessionId || !selectedAgentSlug}
        placeholder={
          !selectedAgentSlug
            ? t('chat.placeholderNoAgent', 'Select an agent to start')
            : !activeSessionId
              ? t('chat.placeholderNoSession', 'Create a session first')
              : t('chat.placeholder', 'Type a message...')
        }
      />
    </div>
  );
}

export default AIChatPanel;
