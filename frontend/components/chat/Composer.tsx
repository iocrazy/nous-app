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
import { Paperclip, Layers, AtSign, Send, X } from 'lucide-react';
import { MentionDropdown, type MentionCandidate } from './MentionDropdown';
import { useComposerPaste } from '../../hooks/useComposerPaste';
import { useComposerDropzone } from '../../hooks/useComposerDropzone';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ComposerAttachment {
  id: string;
  name: string;
  /** Object URL for the thumbnail preview. */
  previewUrl: string;
}

export interface ComposerProps {
  onSend: (text: string, mentionUserIds: string[]) => void;
  onAttachMedia?: () => void;
  /** Called when the user picks, pastes, or drops image files. */
  onAttachFiles?: (files: File[]) => void;
  /**
   * Staged (not-yet-sent) attachments, shown as removable thumbnails above
   * the textarea. Files are uploaded at SEND time, together with the text —
   * picking a file must never fire a message on its own.
   */
  attachments?: ComposerAttachment[];
  onRemoveAttachment?: (id: string) => void;
  /** Called on each input change when content is non-empty. Hook-side throttling applies. */
  onTyping?: () => void;
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
  onAttachFiles,
  attachments = [],
  onRemoveAttachment,
  onTyping,
  disabled = false,
  placeholder,
  members = [],
  agents = [],
}: ComposerProps): React.ReactElement {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const resolvedPlaceholder = placeholder ?? t('chat.composerPlaceholder');

  // ── file attach hooks ──────────────────────────────────────────────────────

  const { onPaste } = useComposerPaste({
    onFiles: (f) => onAttachFiles?.(Array.from(f)),
    disabled,
  });

  const { rootProps, isDragActive } = useComposerDropzone({
    onFiles: (f) => onAttachFiles?.(Array.from(f)),
    disabled,
  });

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
    // Attachments alone are a valid send; empty text is only a no-op when
    // there is nothing staged either.
    if (!text && attachments.length === 0) return;

    // Keep only ids whose @<label> token is still present in the message.
    // Guard against prefix collisions: "@Alice" must not match inside "@Alice Smith"
    // even though "@Alice " (with trailing space) appears there.
    // For each label, build a negative lookahead that excludes any longer tracked
    // label that starts with this label followed by a space (e.g. "Alice Smith"
    // is an extension of "Alice", so the lookahead becomes (?! Smith) which
    // prevents the match when " Smith" immediately follows "@Alice").
    const mentionUserIds: string[] = [];
    const allTrackedLabels = [...mentionMapRef.current.values()];
    for (const [id, label] of mentionMapRef.current.entries()) {
      const esc = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const extensions = allTrackedLabels.filter(
        (l) => l !== label && l.startsWith(label + ' '),
      );
      const negLookahead =
        extensions.length > 0
          ? `(?!${extensions
              .map((l) =>
                l.slice(label.length).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
              )
              .join('|')})`
          : '';
      if (new RegExp(`@${esc}${negLookahead}(?:\\s|$)`).test(text)) {
        mentionUserIds.push(id);
      }
    }

    onSend(text, mentionUserIds);
    el.value = '';
    el.style.height = 'auto';
    mentionMapRef.current.clear();
    setMentionQuery(null);
    setActiveIndex(0);
  }, [disabled, onSend, attachments.length]);

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
        if ((e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) {
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
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
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
      // Signal typing only when there is actual content (don't broadcast on clear)
      if (el.value) {
        onTyping?.();
      }
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
    [onTyping],
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
    // Recalc height — direct value mutation does not fire the input event
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
    // Trigger mention detection
    setMentionQuery('');
    setActiveIndex(0);
  }, [disabled]);

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="relative flex-shrink-0 px-[18px] pb-4 pt-3" {...rootProps}>
      {/* Hidden file input for click-to-attach */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          const fs = e.target.files;
          if (fs && fs.length) {
            onAttachFiles?.(Array.from(fs));
          }
          e.target.value = '';
        }}
      />

      {/* Drag-over overlay */}
      {isDragActive && (
        <div className="absolute inset-0 z-10 rounded-[12px] border-2 border-dashed border-indigo-500/60 bg-indigo-500/[0.06] flex items-center justify-center pointer-events-none">
          <span className="text-[13px] font-medium text-[var(--accent-text)]">
            {t('chat.image.dropHint')}
          </span>
        </div>
      )}

      {/* Input box — relative so MentionDropdown can anchor to it */}
      <div className="relative bg-ink-950 border border-line-strong rounded-[12px] px-3 py-[10px] focus-within:border-indigo-500/50 transition-colors">
        {/* Mention dropdown — floats above the input box */}
        <MentionDropdown
          open={dropdownOpen}
          items={candidates}
          activeIndex={activeIndex}
          onPick={handlePick}
        />

        {/* Staged attachments — removable thumbnails; uploaded on send. */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {attachments.map((a) => (
              <div
                key={a.id}
                className="relative group/att w-[52px] h-[52px] rounded-[8px] overflow-hidden border border-line-strong bg-island-2"
                title={a.name}
              >
                <img
                  src={a.previewUrl}
                  alt={a.name}
                  className="w-full h-full object-cover"
                />
                {onRemoveAttachment && (
                  <button
                    type="button"
                    onClick={() => onRemoveAttachment(a.id)}
                    aria-label={t('chat.image.removeAttachment')}
                    className="absolute top-[2px] right-[2px] w-[16px] h-[16px] rounded-full grid place-items-center bg-black/70 text-white opacity-0 group-hover/att:opacity-100 transition-opacity"
                  >
                    <X size={10} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          disabled={disabled}
          placeholder={resolvedPlaceholder}
          rows={1}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          onPaste={onPaste}
          className={[
            'w-full bg-transparent border-none outline-none resize-none',
            'text-[14px] text-content placeholder:text-content-3 font-[inherit]',
            'leading-[1.55] min-h-[22px] max-h-[120px] overflow-y-auto',
            'disabled:opacity-40 disabled:cursor-not-allowed',
          ].join(' ')}
        />

        {/* Toolbar */}
        <div className="flex items-center gap-1 mt-[9px]">
          {/* Image upload is only wired when the caller passes onAttachFiles
              (conversations feature on). Keep the button hidden otherwise so
              the flag-off path never shows an entry that errors on click. */}
          {onAttachFiles && (
            <button
              type="button"
              disabled={disabled}
              title={t('chat.attachResource')}
              onClick={() => fileInputRef.current?.click()}
              className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-content-3 hover:text-content hover:bg-island-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Paperclip size={14} />
            </button>
          )}
          {/* Layers matches the Resources nav icon — this button inserts a
              file FROM the library, not an image upload (that's the clip). */}
          <button
            type="button"
            disabled={disabled}
            onClick={onAttachMedia}
            title={t('chat.attachMedia')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-content-3 hover:text-content hover:bg-island-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Layers size={14} />
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={handleAtButton}
            title={t('chat.mention')}
            className="w-[30px] h-[30px] rounded-[8px] grid place-items-center text-content-3 hover:text-content hover:bg-island-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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
      <p className="text-[11px] text-content-3 mt-[7px] pl-[2px]">
        {t('chat.composerHint')}
      </p>
    </div>
  );
}
