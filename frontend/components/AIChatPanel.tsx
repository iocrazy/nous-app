import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Plus } from 'lucide-react';
import {
  fetchAgents,
  fetchSessions,
  fetchSessionWithMessages,
  createSession,
  deleteSession,
  sendMessage,
  streamMessage,
  type AIAgent,
  type AISession,
  type AIMessage,
} from '../services/aiService';
import { AgentSelector } from './AgentSelector';
import { SessionList, type SessionItem } from './SessionList';
import { MessageBubble } from './chat/MessageBubble';
import { TypingIndicator } from './chat/TypingIndicator';
import { ChatInput } from './chat/ChatInput';
import { EmptyState } from './chat/EmptyState';

export interface AIChatPanelProps {
  projectId: string;
  contextType?: 'script' | 'storyboard';
  contextId?: string;
  onApplyContent?: (content: string) => void;
  onClose?: () => void;
}

function formatTimestamp(isoString?: string): string {
  if (!isoString) return '';
  return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function AIChatPanel({
  projectId,
  contextType,
  contextId,
  onApplyContent,
  onClose,
}: AIChatPanelProps): React.ReactElement {
  const [agents, setAgents] = useState<AIAgent[]>([]);
  const [sessions, setSessions] = useState<AISession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AIMessage[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  // Initialise: load agents + sessions
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const [fetchedAgents, fetchedSessions] = await Promise.all([
          fetchAgents(projectId).catch(() => []),
          fetchSessions(projectId).catch(() => []),
        ]);

        if (cancelled) return;

        const agentList = Array.isArray(fetchedAgents) ? fetchedAgents : [];
        const sessionList = Array.isArray(fetchedSessions) ? fetchedSessions : [];

        setAgents(agentList);
        setSessions(sessionList);

        if (agentList.length > 0) {
          setSelectedAgentId(agentList[0].id);
        }

        if (sessionList.length > 0) {
          // Auto-select the most recent session
          const firstSession = sessionList[0];
          setActiveSessionId(firstSession.id);
          await loadSessionMessages(firstSession.id, cancelled);
        } else {
          // No sessions — create a new one
          const newSession = await createSession({
            projectId,
            contextType,
            contextId,
            title: 'New conversation',
          });
          if (cancelled) return;
          setSessions([newSession]);
          setActiveSessionId(newSession.id);
        }
      } catch (err) {
        console.error('[AIChatPanel] Init failed:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function loadSessionMessages(sessionId: string, cancelled = false): Promise<void> {
    try {
      const data = await fetchSessionWithMessages(sessionId);
      if (cancelled) return;
      setMessages(data.messages);
    } catch (err) {
      console.error('[AIChatPanel] Failed to load messages:', err);
    }
  }

  const handleSelectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setMessages([]);
    setStreamingContent('');
    await loadSessionMessages(sessionId);
  }, []);

  const handleNewSession = useCallback(async () => {
    try {
      const newSession = await createSession({
        projectId,
        contextType,
        contextId,
        title: 'New conversation',
      });
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(newSession.id);
      setMessages([]);
      setStreamingContent('');
    } catch (err) {
      console.error('[AIChatPanel] Create session failed:', err);
    }
  }, [projectId, contextType, contextId]);

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await deleteSession(sessionId);
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
        console.error('[AIChatPanel] Delete session failed:', err);
      }
    },
    [sessions, activeSessionId],
  );

  const handleSend = useCallback(
    (text: string) => {
      if (!activeSessionId || streaming) return;

      // Optimistically add user message
      const userMsg: AIMessage = {
        id: `tmp-user-${Date.now()}`,
        session_id: activeSessionId,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);
      setStreamingContent('');

      // Abort any previous stream
      abortControllerRef.current?.abort();

      abortControllerRef.current = streamMessage(
        activeSessionId,
        text,
        selectedAgentId ?? undefined,
        (chunk) => {
          setStreamingContent((prev) => prev + chunk);
        },
        (usage) => {
          setStreaming(false);

          // Commit the streamed message into the message list
          setMessages((prev) => [
            ...prev,
            {
              id: `tmp-assistant-${Date.now()}`,
              session_id: activeSessionId,
              role: 'assistant',
              content: streamingContent + '', // captured via closure update below
              agent_id: selectedAgentId ?? undefined,
              prompt_tokens: usage.prompt_tokens,
              completion_tokens: usage.completion_tokens,
              created_at: new Date().toISOString(),
            },
          ]);

          // Reload accurate messages from server to get persisted IDs/content
          void loadSessionMessages(activeSessionId);
          setStreamingContent('');

          // Update session list to reflect new message_count / updated_at
          void fetchSessions(projectId).then((updated) => setSessions(updated)).catch(console.error);
        },
        (error) => {
          console.error('[AIChatPanel] Stream error:', error);
          setStreaming(false);
          setStreamingContent('');
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeSessionId, selectedAgentId, streaming, projectId],
  );

  // Fallback for suggestion clicks in EmptyState
  const handleSuggest = useCallback(
    (suggestion: string) => {
      handleSend(suggestion);
    },
    [handleSend],
  );

  const sessionItems: SessionItem[] = sessions.map((s) => ({
    id: s.id,
    title: s.title,
    updated_at: s.updated_at,
  }));

  const hasMessages = messages.length > 0 || streaming;

  return (
    <div className="w-80 flex-shrink-0 flex flex-col bg-zinc-900 border-l border-zinc-800 h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 flex-shrink-0">
        <span className="text-sm font-medium text-zinc-200 flex-1">AI Chat</span>

        <AgentSelector
          agents={agents}
          selectedId={selectedAgentId}
          onSelect={setSelectedAgentId}
        />

        <button
          type="button"
          onClick={handleNewSession}
          className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
          title="New session"
        >
          <Plus size={15} />
        </button>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
            title="Close"
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
                agentName={msg.agent_name}
                tokens={
                  msg.prompt_tokens != null && msg.completion_tokens != null
                    ? msg.prompt_tokens + msg.completion_tokens
                    : undefined
                }
                timestamp={formatTimestamp(msg.created_at)}
                onApply={
                  msg.role === 'assistant' && onApplyContent
                    ? () => onApplyContent(msg.content)
                    : undefined
                }
              />
            ))}

            {/* Streaming assistant bubble */}
            {streaming && (
              <div className="flex justify-start mb-3">
                <div className="max-w-[85%] rounded-xl bg-zinc-800 text-zinc-200 text-sm leading-relaxed overflow-hidden">
                  {streamingContent ? (
                    <div className="px-3 py-2 whitespace-pre-wrap break-words">
                      {streamingContent}
                    </div>
                  ) : (
                    <TypingIndicator />
                  )}
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
        disabled={streaming || !activeSessionId}
        placeholder={activeSessionId ? 'Type a message...' : 'Create a session first'}
      />
    </div>
  );
}
