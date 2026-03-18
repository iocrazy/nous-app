import React, { useCallback, useEffect, useRef } from 'react';
import { Bot, ChevronRight, ChevronLeft } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  open: boolean;
  onToggle: () => void;
  processing?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

const ChatPanel = React.memo(function ChatPanel({
  open,
  onToggle,
  processing = false,
}: ChatPanelProps) {
  const { chatMessages, addChatMessage, selectedNodeId, characters } =
    useStoryboardStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (open) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, open]);

  const handleSend = useCallback(
    (text: string) => {
      const userMsg = {
        id: `msg-${Date.now()}`,
        role: 'user' as const,
        content: text,
        timestamp: new Date().toISOString(),
      };
      addChatMessage(userMsg);
      // Note: actual AI call would be dispatched here via a hook or service
    },
    [addChatMessage]
  );

  const handleApplyToFrame = useCallback((_messageId: string) => {
    // Apply AI suggestion to selected frame — wired up in integration layer
  }, []);

  const handleRegenerate = useCallback((_messageId: string) => {
    // Re-trigger AI generation — wired up in integration layer
  }, []);

  return (
    <div className="shrink-0 h-full flex z-20">
      {/* Toggle button on the left edge */}
      <button
        onClick={onToggle}
        title={open ? 'Collapse AI panel' : 'Expand AI panel'}
        className="self-center flex items-center justify-center w-6 h-12 bg-gray-800 border border-gray-700 border-r-0 rounded-l-lg text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors shadow-lg"
      >
        {open ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
      </button>

      {/* Panel body */}
      <div
        className={[
          'flex flex-col h-full bg-gray-900 border-l border-gray-700 shadow-2xl transition-all duration-200',
          open ? 'w-80' : 'w-0 overflow-hidden',
        ].join(' ')}
      >
        {open && (
          <>
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <div className="flex items-center gap-2">
                <Bot size={16} className="text-blue-400" />
                <h2 className="text-sm font-semibold text-gray-100">AI Assistant</h2>
              </div>
              <button
                onClick={onToggle}
                title="Collapse"
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
              >
                <ChevronRight size={15} />
              </button>
            </div>

            {/* Selected frame indicator */}
            {selectedNodeId && (
              <div className="px-4 py-2 bg-blue-900/30 border-b border-blue-800/40">
                <p className="text-xs text-blue-300">
                  Selected: Frame {String(selectedNodeId).slice(-6)}
                </p>
              </div>
            )}

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
              {chatMessages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center py-8">
                  <Bot size={32} className="text-gray-700 mb-3" />
                  <p className="text-sm text-gray-500">Ask me anything about your storyboard</p>
                  <p className="text-xs text-gray-600 mt-1">
                    Use @name to mention characters
                  </p>
                </div>
              )}
              {chatMessages.map((msg) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  onApplyToFrame={
                    msg.role === 'assistant' && selectedNodeId
                      ? handleApplyToFrame
                      : undefined
                  }
                  onRegenerate={
                    msg.role === 'assistant' ? handleRegenerate : undefined
                  }
                />
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <ChatInput
              onSend={handleSend}
              disabled={processing}
              characters={characters}
            />
          </>
        )}
      </div>
    </div>
  );
});

export default ChatPanel;
