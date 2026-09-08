/**
 * QuestionCard — the one typed-question card (phase 2a §1). Mounted by the
 * issue NeedsInputCard, the cockpit waiting state, the Task Center feed and
 * the chat bubble; none of them draw their own buttons.
 *
 * Contract: an option button answers with its LABEL verbatim (that is what
 * the backend's `answer_matches` compares) plus the question id; free text
 * (when allowed) answers with the typed text. While an answer is in flight
 * everything is disabled; a rejection (the endpoint's typed 4xx — e.g.
 * `budget_still_exhausted`) is shown on the card and the card re-enables so
 * the human can pick again. Once `answered`, the card is read-only with the
 * pick highlighted; `superseded` greys it out ("No longer waiting").
 *
 * Colours are semantic tokens only (ok / warn / ink) — see index.css @theme.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { TypedQuestion } from './questionTypes';

export interface QuestionCardProps {
  question: TypedQuestion;
  /** Resolves when the answer was delivered; rejects with the typed error. */
  onAnswer: (value: string, answerTo: string) => Promise<void>;
  /** Task Center row: tighter paddings, no prompt (the row shows it). */
  compact?: boolean;
  /** Externally held pending state (e.g. the feed row waiting for its
   *  refetch): everything stays disabled while true. */
  disabled?: boolean;
}

export const QuestionCard: React.FC<QuestionCardProps> = ({ question, onAnswer, compact = false, disabled = false }) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const answered = question.answered;
  const superseded = answered?.superseded === true;
  const locked = pending || disabled || !!answered;

  const submit = async (value: string) => {
    if (locked) return;
    const v = value.trim();
    if (!v) return;
    setPending(true);
    setError(null);
    try {
      await onAnswer(v, question.id);
      setDraft('');
    } catch (err) {
      console.error('[QuestionCard] answer failed', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  const pad = compact ? 'px-2 py-1.5' : 'px-3 py-2.5';
  return (
    <div
      data-testid="question-card"
      data-question-id={question.id}
      data-question-kind={question.kind}
      className={`rounded-md border ${answered ? 'border-ink-800 bg-ink-900/40' : 'border-warn-line bg-warn-soft'} ${pad} space-y-2 ${superseded ? 'opacity-60' : ''}`}
    >
      {!compact && question.prompt && (
        <p className="text-[14px] text-ink-200 leading-relaxed whitespace-pre-wrap break-words">{question.prompt}</p>
      )}
      {question.options.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {question.options.map((o) => {
            const picked = answered?.value === o.label;
            const tone = picked
              ? 'bg-ok-soft border-ok-line text-ok'
              : answered
                ? 'border-ink-800 text-ink-500'
                : 'border-warn-line text-warn hover:bg-warn-soft';
            return (
              <button
                key={o.label}
                type="button"
                data-testid="question-option"
                title={o.description}
                disabled={locked}
                onClick={() => void submit(o.label)}
                className={`rounded border px-2.5 py-1 text-[13px] disabled:cursor-not-allowed disabled:opacity-80 ${tone}`}
              >
                {o.label}
              </button>
            );
          })}
        </div>
      )}
      {question.allowFreeText && !answered && (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={draft}
            disabled={pending || disabled}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) void submit(draft);
            }}
            placeholder={t('question.orType', 'Or type your own answer…')}
            className="flex-1 min-w-0 rounded border border-ink-800 bg-ink-900/60 px-2 py-1 text-[13px] text-ink-200 placeholder:text-ink-600 focus:outline-none focus:border-warn-line disabled:opacity-60"
          />
          <button
            type="button"
            disabled={locked || !draft.trim()}
            onClick={() => void submit(draft)}
            className="shrink-0 rounded border border-warn-line px-2.5 py-1 text-[13px] text-warn hover:bg-warn-soft disabled:opacity-50"
          >
            {t('question.answer', 'Answer')}
          </button>
        </div>
      )}
      {answered && (
        <p className="text-[12px] text-ink-500">
          {superseded ? t('question.superseded', 'No longer waiting') : t('question.answered', 'Answered')}
        </p>
      )}
      {error && (
        <p data-testid="question-error" className="text-[12px] text-danger break-words">
          {error}
        </p>
      )}
    </div>
  );
};

export default QuestionCard;
