import React, { useRef, useCallback, KeyboardEvent } from 'react';
import { Send, Paperclip } from 'lucide-react';

export interface ChatInputProps {
  onSend: (message: string) => void;
  onAttach?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

const MAX_ROWS = 5;
const LINE_HEIGHT_PX = 24;

export function ChatInput({
  onSend,
  onAttach,
  disabled = false,
  placeholder = 'Type a message...',
}: ChatInputProps): React.ReactElement {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = MAX_ROWS * LINE_HEIGHT_PX;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const value = textareaRef.current?.value.trim() ?? '';
        if (!value || disabled) return;
        onSend(value);
        if (textareaRef.current) {
          textareaRef.current.value = '';
          textareaRef.current.style.height = 'auto';
        }
      }
    },
    [disabled, onSend],
  );

  const handleSendClick = useCallback(() => {
    const value = textareaRef.current?.value.trim() ?? '';
    if (!value || disabled) return;
    onSend(value);
    if (textareaRef.current) {
      textareaRef.current.value = '';
      textareaRef.current.style.height = 'auto';
    }
  }, [disabled, onSend]);

  return (
    <div className="flex items-end gap-2 p-2 bg-zinc-900 border-t border-zinc-700/50">
      {onAttach && (
        <button
          type="button"
          onClick={onAttach}
          disabled={disabled}
          title="Attach reference"
          className="flex-shrink-0 p-1.5 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Paperclip size={16} />
        </button>
      )}

      <textarea
        ref={textareaRef}
        rows={1}
        disabled={disabled}
        placeholder={placeholder}
        onInput={adjustHeight}
        onKeyDown={handleKeyDown}
        className="
          flex-1 resize-none rounded-lg px-3 py-2
          bg-zinc-800 border border-zinc-700
          text-sm text-zinc-200 placeholder-zinc-500
          focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50
          disabled:opacity-40 disabled:cursor-not-allowed
          leading-6 min-h-[36px]
        "
        style={{ lineHeight: `${LINE_HEIGHT_PX}px` }}
      />

      <button
        type="button"
        onClick={handleSendClick}
        disabled={disabled}
        title="Send message"
        className="
          flex-shrink-0 p-1.5 rounded-lg
          bg-indigo-600 hover:bg-indigo-500
          text-white transition-colors
          disabled:opacity-40 disabled:cursor-not-allowed
        "
      >
        <Send size={16} />
      </button>
    </div>
  );
}
