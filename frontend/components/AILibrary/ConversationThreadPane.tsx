import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, MessagesSquare } from 'lucide-react';
import type { AIChatMessage, ChatSessionWithMessages } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { MarkdownBody } from './MarkdownBody';

// Right-pane conversation transcript for the grouped Runs tab: selecting a
// conversation group shows the session's REAL message history as one coherent
// Q&A thread (paperclip's RunTranscriptView model — read the whole exchange in
// place), instead of forcing the user to open each turn's run detail. Per-turn
// engineering detail (tokens / metadata) stays on the expanded child cards.

function formatMessageTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

const UserBubble: React.FC<{ msg: AIChatMessage }> = ({ msg }) => (
  <div className="flex justify-end">
    <div className="max-w-[85%] rounded-xl rounded-br-sm border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-2">
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-100">
        {msg.content}
      </p>
      <p className="mt-1 text-right text-[10px] text-ink-500">
        {formatMessageTime(msg.created_at)}
      </p>
    </div>
  </div>
);

const AssistantBubble: React.FC<{ msg: AIChatMessage; agentName?: string | null }> = ({
  msg,
  agentName,
}) => (
  <div className="flex justify-start">
    <div className="max-w-[92%] rounded-xl rounded-bl-sm border border-ink-800 bg-ink-900/60 px-3 py-2">
      {agentName && (
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
          {agentName}
        </p>
      )}
      <MarkdownBody source={msg.content} />
      <p className="mt-1 text-[10px] text-ink-500">{formatMessageTime(msg.created_at)}</p>
    </div>
  </div>
);

export const ConversationThreadPane: React.FC<{
  conversationId: string;
  agentName?: string | null;
  onBack?: () => void;
}> = ({ conversationId, agentName, onBack }) => {
  const { t } = useTranslation();
  const [session, setSession] = useState<ChatSessionWithMessages | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await aiLibraryService.getChatSession(conversationId);
      setSession(s);
      setError(null);
    } catch (err) {
      console.error('[ConversationThreadPane] getChatSession failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [conversationId]);

  useEffect(() => {
    setSession(null);
    setError(null);
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        {error}
      </div>
    );
  }
  if (!session) {
    return (
      <p className="text-sm text-ink-500">
        {t('aiLibrary.agents.runs.threadLoading', 'Loading conversation...')}
      </p>
    );
  }

  // The thread is the user-visible exchange — hide system/tool plumbing.
  const visible = (session.messages || []).filter(
    (m) => m.role === 'user' || m.role === 'assistant',
  );

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-2">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 text-sm text-ink-400 hover:text-ink-200 md:hidden"
          >
            <ArrowLeft size={14} />
            {t('common.back', 'Back')}
          </button>
        )}
        <MessagesSquare size={15} className="text-ink-400" />
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink-100">
          {session.title ||
            t('aiLibrary.agents.runs.conversation', 'Conversation')}
        </h3>
        <span className="text-[10px] tabular-nums text-ink-500">
          {t('aiLibrary.agents.runs.turns', {
            defaultValue: '{{count}} turns',
            count: visible.filter((m) => m.role === 'user').length,
          })}
        </span>
      </header>
      <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-950/40 p-3">
        {visible.length === 0 && (
          <p className="py-6 text-center text-sm text-ink-500">
            {t('aiLibrary.agents.runs.threadEmpty', 'No messages in this conversation.')}
          </p>
        )}
        {visible.map((msg) =>
          msg.role === 'user' ? (
            <UserBubble key={msg.id} msg={msg} />
          ) : (
            <AssistantBubble key={msg.id} msg={msg} agentName={agentName} />
          ),
        )}
      </div>
    </div>
  );
};
