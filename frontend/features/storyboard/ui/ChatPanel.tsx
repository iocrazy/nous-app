import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, Loader2, MessageCircle, Send, User, X } from 'lucide-react';
import { chatWithAI } from '../../../services/storyboardService';

// ─── Types ───────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  projectId: string;
  onClose: () => void;
}

interface ChatMessage {
  readonly role: 'user' | 'assistant';
  readonly content: string;
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ChatPanel({ projectId, onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-dismiss error after 5 seconds
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), 5000);
    return () => clearTimeout(timer);
  }, [error]);

  const handleSend = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    const userMessage: ChatMessage = { role: 'user', content: trimmed };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setLoading(true);

    try {
      const result = await chatWithAI(projectId, trimmed);
      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: result.response,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to get AI response';
      setError(message);
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  }, [input, loading, projectId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="flex h-full w-80 flex-col border-l border-zinc-700 bg-zinc-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-3">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-medium text-zinc-100">
            AI Assistant
          </span>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
          aria-label="Close chat panel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-4">
        {messages.length === 0 && !loading && (
          <EmptyState />
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}

        {loading && <TypingIndicator />}

        <div ref={messagesEndRef} />
      </div>

      {/* Error toast */}
      {error && (
        <div className="mx-3 mb-2 rounded-md bg-red-900/60 px-3 py-2 text-xs text-red-200">
          {error}
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-zinc-700 p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your storyboard..."
            rows={1}
            className="max-h-24 flex-1 resize-none rounded-md border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 outline-none focus:border-indigo-500"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading}
            className="rounded-md bg-indigo-600 p-2 text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-1.5 text-[10px] text-zinc-500">
          Enter to send, Shift+Enter for newline
        </p>
      </div>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center">
      <Bot className="h-8 w-8 text-zinc-600" />
      <p className="text-sm text-zinc-500">
        Ask me about your storyboard...
      </p>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
          isUser ? 'bg-indigo-600' : 'bg-zinc-700'
        }`}
      >
        {isUser ? (
          <User className="h-3.5 w-3.5 text-white" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-zinc-300" />
        )}
      </div>
      <div
        className={`max-w-[75%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? 'bg-indigo-600 text-white'
            : 'bg-zinc-800 text-zinc-200'
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-700">
        <Bot className="h-3.5 w-3.5 text-zinc-300" />
      </div>
      <div className="flex items-center gap-1.5 rounded-lg bg-zinc-800 px-3 py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-400" />
        <span className="text-xs text-zinc-400">Thinking...</span>
      </div>
    </div>
  );
}
