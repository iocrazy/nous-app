import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, ChevronDown, Loader2, MessageCircle, Send, User, Wand2, X } from 'lucide-react';
import { chatWithAI } from '../../../services/storyboardService';
import { useSkillSelector } from '../../../hooks/useSkillSelector';
import type { Skill } from '../../../types';

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
  const [showSkillDropdown, setShowSkillDropdown] = useState(false);

  const { groups, selectedSkill, selectSkill, filterText, setFilterText } =
    useSkillSelector(projectId);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), 5000);
    return () => clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    if (!showSkillDropdown) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowSkillDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showSkillDropdown]);

  const handleSend = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    const userMessage: ChatMessage = { role: 'user', content: trimmed };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setLoading(true);

    try {
      const result = await chatWithAI(
        projectId,
        trimmed,
        undefined,
        selectedSkill?.id,
      );
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
  }, [input, loading, projectId, selectedSkill]);

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
          <span className="text-sm font-medium text-zinc-100">AI Assistant</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setShowSkillDropdown(!showSkillDropdown)}
              className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors ${
                selectedSkill
                  ? 'bg-violet-900/40 text-violet-300 hover:bg-violet-900/60'
                  : 'text-zinc-400 hover:bg-zinc-700 hover:text-zinc-100'
              }`}
              aria-label="Select skill"
              aria-expanded={showSkillDropdown}
              role="combobox"
            >
              <Wand2 className="h-3 w-3" />
              <span className="max-w-[80px] truncate">
                {selectedSkill ? selectedSkill.name : 'Skill'}
              </span>
              <ChevronDown className="h-3 w-3" />
            </button>

            {showSkillDropdown && (
              <SkillDropdown
                groups={groups}
                filterText={filterText}
                onFilterChange={setFilterText}
                onSelect={(skill) => {
                  selectSkill(skill);
                  setShowSkillDropdown(false);
                  setFilterText('');
                }}
                onClear={() => {
                  selectSkill(null);
                  setShowSkillDropdown(false);
                  setFilterText('');
                }}
                selectedId={selectedSkill?.id}
              />
            )}
          </div>

          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
            aria-label="Close chat panel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-4">
        {messages.length === 0 && !loading && <EmptyState />}
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

      {/* Active skill badge */}
      {selectedSkill && (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-md bg-violet-900/30 px-3 py-1.5">
          <Wand2 className="h-3 w-3 text-violet-400" />
          <span className="flex-1 truncate text-xs text-violet-300">
            Using: {selectedSkill.name}
          </span>
          <button
            onClick={() => selectSkill(null)}
            className="text-violet-400 hover:text-violet-200"
            aria-label="Remove active skill"
          >
            <X className="h-3 w-3" />
          </button>
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

interface SkillDropdownProps {
  groups: readonly { label: string; skills: readonly Skill[] }[];
  filterText: string;
  onFilterChange: (text: string) => void;
  onSelect: (skill: Skill) => void;
  onClear: () => void;
  selectedId?: string;
}

function SkillDropdown({ groups, filterText, onFilterChange, onSelect, onClear, selectedId }: SkillDropdownProps) {
  return (
    <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-zinc-700 bg-zinc-800 shadow-xl">
      <div className="border-b border-zinc-700 p-2">
        <input
          type="text"
          value={filterText}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="Search skills..."
          className="w-full rounded bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 outline-none"
          autoFocus
        />
      </div>

      <div className="max-h-64 overflow-y-auto p-1">
        <button
          onClick={onClear}
          className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${
            !selectedId ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-700/50'
          }`}
        >
          No Skill
        </button>

        {groups.map((group) => (
          <div key={group.label}>
            <div className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-zinc-500">
              {group.label}
            </div>
            {group.skills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => onSelect(skill)}
                className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${
                  selectedId === skill.id
                    ? 'bg-violet-900/40 text-violet-200'
                    : 'text-zinc-300 hover:bg-zinc-700/50'
                }`}
              >
                <span>{skill.icon}</span>
                <div className="flex-1 truncate">
                  <div className="truncate">{skill.name}</div>
                  {skill.description && (
                    <div className="truncate text-[10px] text-zinc-500">
                      {skill.description}
                    </div>
                  )}
                </div>
              </button>
            ))}
          </div>
        ))}

        {groups.length === 0 && (
          <div className="px-2 py-4 text-center text-xs text-zinc-500">
            No skills found
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center">
      <Bot className="h-8 w-8 text-zinc-600" />
      <p className="text-sm text-zinc-500">Ask me about your storyboard...</p>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${isUser ? 'bg-indigo-600' : 'bg-zinc-700'}`}>
        {isUser ? <User className="h-3.5 w-3.5 text-white" /> : <Bot className="h-3.5 w-3.5 text-zinc-300" />}
      </div>
      <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm leading-relaxed ${isUser ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-200'}`}>
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
