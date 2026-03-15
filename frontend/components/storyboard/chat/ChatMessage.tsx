import React, { useCallback } from 'react';
import { RefreshCw, Sparkles } from 'lucide-react';
import { ChatMessage as ChatMessageType } from '../../../stores/storyboardStore';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ChatMessageProps {
  message: ChatMessageType;
  onApplyToFrame?: (messageId: string) => void;
  onRegenerate?: (messageId: string) => void;
}

// ─── Markdown-lite renderer ───────────────────────────────────────────────────

function renderContent(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} className="px-1 py-0.5 bg-gray-700 rounded text-xs font-mono">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

const ChatMessage = React.memo(function ChatMessage({
  message,
  onApplyToFrame,
  onRegenerate,
}: ChatMessageProps) {
  const isUser = message.role === 'user';

  const handleApply = useCallback(() => {
    onApplyToFrame?.(message.id);
  }, [message.id, onApplyToFrame]);

  const handleRegenerate = useCallback(() => {
    onRegenerate?.(message.id);
  }, [message.id, onRegenerate]);

  return (
    <div className={['flex flex-col gap-1', isUser ? 'items-end' : 'items-start'].join(' ')}>
      <div
        className={[
          'max-w-[85%] px-3 py-2 rounded-2xl text-sm leading-relaxed',
          isUser
            ? 'bg-blue-600 text-white rounded-br-sm'
            : 'bg-gray-700 text-gray-100 rounded-bl-sm',
        ].join(' ')}
      >
        {renderContent(message.content)}
      </div>

      {/* Action buttons for assistant messages */}
      {!isUser && (onApplyToFrame || onRegenerate) && (
        <div className="flex items-center gap-1 ml-1">
          {onApplyToFrame && (
            <button
              onClick={handleApply}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white border border-gray-700 transition-colors"
            >
              <Sparkles size={10} />
              Apply to Frame
            </button>
          )}
          {onRegenerate && (
            <button
              onClick={handleRegenerate}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white border border-gray-700 transition-colors"
            >
              <RefreshCw size={10} />
              Regenerate
            </button>
          )}
        </div>
      )}

      <span className="text-[10px] text-gray-600 px-1">{formatTime(message.timestamp)}</span>
    </div>
  );
});

export default ChatMessage;
