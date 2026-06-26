/**
 * Composer — message input for Team Chat.
 *
 * Behaviours:
 *   - Enter submits (calls onSend(text, mentionUserIds) then clears)
 *   - Shift+Enter inserts a newline
 *   - Empty sends are ignored (after trim)
 *   - disabled disables both textarea and send button
 *   - Typing @<token> at start-of-text or after whitespace opens MentionDropdown
 *   - ArrowUp/Down navigate candidates; Enter/Tab picks; Esc closes
 *   - Picking a user inserts @<label> and tracks the user_id
 *   - Picking an agent inserts @<slug> (agent dispatch uses slug text-parse)
 *   - On submit: only user ids whose @<label> is still present are sent
 *
 * Purely presentational — no data fetching.
 */

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip, Image, AtSign, Send } from 'lucide-react';
import { MentionDropdown, type MentionCandidate } from './MentionDropdown';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ComposerProps {
  onSend: (text: string, mentionUserIds: string[]) => void;
  onAttachMedia?: () => void;
  disabled?: boolean;
  placeholder?: string;
  members?: { user_id: string; label: string }[];
  agents?: { slug: string; label: string }[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Detect an active @-mention token: the last `@` that is either at the
 * start of the string or preceded by whitespace, with no whitespace
 * between it and the caret.
 *
 * Returns the query string (text after `@`) and the byte-offset of the `@`
 * in the full string, or `null` if no active token.
 */
function _detectMention(
  value: string,
  caretPos: number,
): { query: string; atPos: number } | null {
  const textUpToCaret = value.slice(0, caretPos);
  const match = textUpToCaret.match(/(^|\s)@(\S*)$/);
  if (!match) return null;
  const atPos = match.index! + match[1].length;
  return { query: match[2], atPos };
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Composer({
  onSend,
  onAttachMedia,
  disabled = false,
  placeholder,
  members = [],
  agents = [],
}: ComposerProps): React.ReactElement {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const resolvedPlaceholder = placeholder ?? t('chat.composerPlaceholder');

  // mention state
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  // map: user_id → label  (for the "still-present" filter on submit)
  const mentionMapRef = useRef<Map<string, string>>(new Map());

  // ── candidates ─────────────────────────────────────────────────────────────

  const candidates = useMemo<MentionCandidate[]>(() => {
    if (mentionQuery === null) return [];
    const q = mentionQuery.toLowerCase();
    const userCandidates: MentionCandidate[] = members
      .filter((m) => m.label.toLowerCase().includes(q))
      .map((m) => ({ kind: 'user', id: m.user_id, label: m.label }));
    const agentCandidates: MentionCandidate[] = agents
      .filter((a) => a.label.toLowerCase().includes(q))
      .map((a) => ({ kind: 'agent', slug: a.slug, label: a.label }));
    return [...userCandidates, ...agentCandidates].slice(0, 8);
  }, [mentionQuery, members, agents]);

  const dropdownOpen = mentionQuery !== null && candidates.length > 0;

  // ── pick ───────────────────────────────────────────────────────────────────

  const handlePick = useCallback((c: MentionCandidate) => {
    const el = textareaRef.current;
    if (!el) return;
    const cursor = el.selectionStart ?? el.value.length;
    const detected = _detectMention(el.value, cursor);
    if (detected) {
      const { atPos } = detected;
      const insertion =
        c.kind === 'user' ? `@${c.label} ` : `@${c.slug} `;
      el.value =
        el.value.slice(0, atPos) + insertion + el.value.slice(cursor);
      const newCursor = atPos + insertion.length;
      el.setSelectionRange(newCursor, newCursor);
      // Recompute height after text change
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
      if (c.kind === 'user') {
        mentionMapRef.current.set(c.id, c.label);
      }
    }
    setMentionQuery(null);
    setActiveIndex(0);
    el.focus();
  }, []);

  // ── send ───────────────────────────────────────────────────────────────────

  const handleSend = useCallback(() => {
    const el = textareaRef.current;
    if (!el || disabled) return;
    const text = el.value.trim();
    if (!text) return;

    // Keep only ids whose @<label> text is still present in the message
    const mentionUserIds: string[] = [];
    for (const [id, label] of mentionMapRef.current.entries()) {
      if (text.includes(`@${label}`)) {
        mentionUserIds.push(id);
      }
    }

    onSend(text, mentionUserIds);
    el.value = '';
    el.style.height = 'auto';
    mentionMapRef.current.clear();
    setMentionQuery(null);
    setActiveIndex(0);
  }, [disabled, onSend]);

  // ── keyboard ───────────────────────────────────────────────────────────────

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (dropdownOpen) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setActiveIndex((i) => Math.min(i + 1, candidates.length - 1));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setActiveIndex((i) => Math.max(i - 1, 0));
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          const candidate = candidates[activeIndex];
          if (candidate) handlePick(candidate);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setMentionQuery(null);
          setActiveIndex(0);
          return;
        }
      }

      // Normal composer behaviour when dropdown is closed
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [dropdownOpen, candidates, activeIndex, handlePick, handleSend],
  );

  // ── input (auto-grow + mention detection) ──────────────────────────────────

  const handleInput = useCallback(
    (e: React.FormEvent<HTMLTextAreaElement>) => {
      const el = e.currentTarget;
      // Auto-grow up to ~5 lines
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
      // Detect active @-token
      const cursor = el.selectionStart ?? el.value.length;
      const detected = _detectMention(el.value, cursor);
      if (detected) {
        setMentionQuery(detected.query);
        setActiveIndex(0);
      } else {
        setMentionQuery(null);
      }
    },
    [],
  );

  // ── @ toolbar button ───────────────────────────────────────────────────────

  const handleAtButton = useCallback(() => {
    const el = textareaRef.current;
    if (!el || disabled) return;
    el.focus();
    const cursor = el.selectionStart ?? el.value.length;
    const before = el.value.slice(0, cursor);
    const after = el.value.slice(cursor);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const insertion = (needsSpace ? ' ' : '') + '@';
    el.value = before + insertion + after;
    const newCursor = cursor + insertion.length;
    el.setSelectionRange(newCursor, newCursor);
    // Trigger mention detection
    setMentionQuery('');
    setActiveIndex(0);
  }, [disabled]);

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex-shrink-0 px-[18px] pb-4 pt-3">
      {/* Input box — relative so MentionDropdown can anchor to it */}
      <div className="relative bg-ink-950 border border-white/[.12] rounded-[12px] px-3 py-[10px] focus-within:border-indigo-500/50 transition-colors">
        {/* Mention dropdown — floats above the input box */}
        <MentionDropdown
          open={dropdownOpen}
          items={candidates}
          activeIndex={activeIndex}
          onPick={handlePick}
        />

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
            onClick={onAttachMedia}
            title={t('chat.attachMedia')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-[#74747e] hover:text-[#e7e7ea] hover:bg-[#17171b] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Image size={14} />
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={handleAtButton}
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
