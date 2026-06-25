/**
 * Composer — message input for Team Chat.
 *
 * Behaviours:
 *   - Enter submits (calls onSend(text) then clears)
 *   - Shift+Enter inserts a newline
 *   - Empty sends are ignored (after trim)
 *   - disabled disables both textarea and send button
 *
 * Purely presentational — no data fetching.
 */

import React, { useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip, Image, AtSign, Send } from 'lucide-react';

export interface ComposerProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function Composer({
  onSend,
  disabled = false,
  placeholder,
}: ComposerProps): React.ReactElement {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const resolvedPlaceholder = placeholder ?? t('chat.composerPlaceholder');

  const handleSend = useCallback(() => {
    const el = textareaRef.current;
    if (!el || disabled) return;
    const text = el.value.trim();
    if (!text) return;
    onSend(text);
    el.value = '';
    // Reset height after clear
    el.style.height = 'auto';
  }, [disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  /** Auto-grow textarea up to a max of ~5 lines */
  const handleInput = useCallback((e: React.FormEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  return (
    <div className="flex-shrink-0 px-[18px] pb-4 pt-3">
      {/* Input box */}
      <div className="bg-ink-950 border border-white/[.12] rounded-[12px] px-3 py-[10px] focus-within:border-indigo-500/50 transition-colors">
        <textarea
          ref={textareaRef}
          disabled={disabled}
          placeholder={resolvedPlaceholder}
          rows={1}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          className={[
            'w-full bg-transparent border-none outline-none resize-none',
            'text-[14px] text-[#e7e7ea] placeholder:text-[#74747e] font-[inherit]',
            'leading-[1.55] min-h-[22px] max-h-[120px] overflow-y-auto',
            'disabled:opacity-40 disabled:cursor-not-allowed',
          ].join(' ')}
        />

        {/* Toolbar */}
        <div className="flex items-center gap-1 mt-[9px]">
          <button
            type="button"
            disabled={disabled}
            title={t('chat.attachResource')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-[#74747e] hover:text-[#e7e7ea] hover:bg-[#17171b] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Paperclip size={14} />
          </button>
          <button
            type="button"
            disabled={disabled}
            title={t('chat.attachMedia')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-[#74747e] hover:text-[#e7e7ea] hover:bg-[#17171b] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Image size={14} />
          </button>
          <button
            type="button"
            disabled={disabled}
            title={t('chat.mention')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-[#74747e] hover:text-[#e7e7ea] hover:bg-[#17171b] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <AtSign size={14} />
          </button>

          <div className="flex-1" />

          {/* Send button */}
          <button
            type="button"
            onClick={handleSend}
            disabled={disabled}
            title={t('chat.send')}
            className="w-[32px] h-[32px] rounded-[8px] grid place-items-center bg-indigo-500 hover:bg-indigo-600 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors border-none"
          >
            <Send size={14} />
          </button>
        </div>
      </div>

      {/* Hint */}
      <p className="text-[11px] text-[#74747e] mt-[7px] pl-[2px]">
        {t('chat.composerHint')}
      </p>
    </div>
  );
}
