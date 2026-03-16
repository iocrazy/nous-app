import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Send } from 'lucide-react';
import { StoryboardCharacter } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  characters?: StoryboardCharacter[];
}

// ─── Component ────────────────────────────────────────────────────────────────

const ChatInput = React.memo(function ChatInput({
  onSend,
  disabled = false,
  characters = [],
}: ChatInputProps) {
  const [value, setValue] = useState('');
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionAnchor, setMentionAnchor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filteredChars = mentionQuery !== null
    ? characters.filter((c) =>
        c.name.toLowerCase().includes(mentionQuery.toLowerCase())
      )
    : [];

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const text = e.target.value;
      setValue(text);

      // Detect @mention trigger
      const cursor = e.target.selectionStart ?? text.length;
      const beforeCursor = text.slice(0, cursor);
      const atIdx = beforeCursor.lastIndexOf('@');

      if (atIdx !== -1) {
        const fragment = beforeCursor.slice(atIdx + 1);
        if (!fragment.includes(' ')) {
          setMentionQuery(fragment);
          setMentionAnchor(atIdx);
          return;
        }
      }
      setMentionQuery(null);
    },
    []
  );

  const insertMention = useCallback(
    (characterName: string) => {
      const before = value.slice(0, mentionAnchor);
      const after = value.slice(
        (inputRef.current?.selectionStart ?? mentionAnchor)
      );
      const newValue = `${before}@${characterName} ${after}`;
      setValue(newValue);
      setMentionQuery(null);
      inputRef.current?.focus();
    },
    [value, mentionAnchor]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Escape') {
        setMentionQuery(null);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey && !mentionQuery) {
        e.preventDefault();
        const trimmed = value.trim();
        if (!trimmed || disabled) return;
        onSend(trimmed);
        setValue('');
      }
    },
    [value, disabled, onSend, mentionQuery]
  );

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue('');
  }, [value, disabled, onSend]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (inputRef.current && !inputRef.current.contains(e.target as Node)) {
        setMentionQuery(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative">
      {/* Mention autocomplete dropdown */}
      {mentionQuery !== null && filteredChars.length > 0 && (
        <div className="absolute bottom-full mb-1 left-0 right-0 bg-gray-800 border border-gray-700 rounded-xl shadow-xl overflow-hidden z-30">
          {filteredChars.slice(0, 5).map((char) => (
            <button
              key={char.id}
              onMouseDown={(e) => {
                e.preventDefault();
                insertMention(char.name);
              }}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
            >
              <span className="text-blue-400">@</span>
              {char.name}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 p-3 border-t border-gray-700">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Processing...' : 'Ask AI...'}
          disabled={disabled}
          className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-xl text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          title="Send (Enter)"
          className="p-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={15} />
        </button>
      </div>
    </div>
  );
});

export default ChatInput;
