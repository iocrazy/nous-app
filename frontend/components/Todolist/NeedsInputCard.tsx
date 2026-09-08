/**
 * A2 —— 详情页提问卡。
 *
 * When a dispatch declares `needs_input` the issue parks at needs_followup and
 * the workflow suspends on DBOS.recv — a reply resumes it in place. Until now
 * that hand-off was only visible in the Task Center feed; here it sits at the
 * top of the issue the question is about, so answering never means leaving the
 * page you are reading.
 *
 * Failure typing mirrors TaskCenter's NeedsInputSection (CLAUDE.md「触发路径
 * 必须类型化失败回显」): AgentNotDispatchedError means the message WAS saved
 * but no turn started — show the dedicated notice and deliberately do NOT
 * restore the draft, because resubmitting would post a duplicate. A plain
 * error is a genuine failure: restore the draft so it can be retried.
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HelpCircle } from 'lucide-react';

import { AgentNotDispatchedError } from '../../services/issueMessageService';
import { QuestionCard } from './QuestionCard';
import type { TypedQuestion } from './questionTypes';

interface NeedsInputCardProps {
  /** The agent's stated reason for stopping (`execution_state.outcome_reason`). */
  question: string | null;
  agentName?: string;
  /** Posts the reply. Rejecting with AgentNotDispatchedError selects the
   *  "saved but nothing started" branch. */
  onSubmit: (body: string) => Promise<void>;
  /** Phase 2a: the typed question from the marker. With options the card
   *  mounts QuestionCard (buttons) instead of the textarea; the prompt is
   *  still `question` above. */
  typed?: TypedQuestion | null;
  /** Answer the typed question (label or free text + its id). */
  onAnswer?: (value: string, answerTo: string) => Promise<void>;
}

export const NeedsInputCard: React.FC<NeedsInputCardProps> = ({
  question,
  agentName,
  onSubmit,
  typed = null,
  onAnswer,
}) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [notDispatched, setNotDispatched] = useState(false);

  const submit = async () => {
    const body = draft.trim();
    if (!body || pending) return;
    setDraft('');
    setPending(true);
    setNotDispatched(false);
    try {
      await onSubmit(body);
    } catch (err) {
      if (err instanceof AgentNotDispatchedError) {
        setNotDispatched(true);
      } else {
        console.error('[NeedsInputCard] reply failed', err);
        setDraft(body);
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <section
      data-testid="needs-input-card"
      className="mt-5 rounded-lg border border-warn-line bg-warn-soft px-3 py-3 space-y-2"
    >
      <h2 className="flex items-center gap-1.5 text-[13px] font-medium text-warn">
        <HelpCircle size={14} />
        {t('issueDetail.agentAsking', 'Agent needs your answer')}
        {agentName && <span className="text-ink-500 font-normal">· {agentName}</span>}
      </h2>
      {question && (
        <p className="text-[14px] text-ink-200 leading-relaxed whitespace-pre-wrap break-words">
          {question}
        </p>
      )}
      {typed && typed.options.length > 0 && onAnswer ? (
        <QuestionCard
          question={{ ...typed, prompt: typed.prompt === question ? '' : typed.prompt }}
          onAnswer={async (value, answerTo) => {
            setNotDispatched(false);
            try {
              await onAnswer(value, answerTo);
            } catch (err) {
              if (err instanceof AgentNotDispatchedError) {
                setNotDispatched(true);
                return;
              }
              throw err;
            }
          }}
        />
      ) : (
      <textarea
        value={draft}
        disabled={pending}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={t('issueDetail.replyHere', 'Reply here…')}
        rows={2}
        className="w-full rounded border border-ink-800 bg-ink-900/60 px-2 py-1.5 text-[14px] text-ink-200 placeholder:text-ink-600 focus:outline-none focus:border-warn-line disabled:opacity-60"
      />
      )}
      {notDispatched && (
        <p data-testid="needs-input-not-dispatched" className="text-[12px] text-warn">
          {t('taskCenter.answerNotDispatched')}
        </p>
      )}
      {!(typed && typed.options.length > 0 && onAnswer) && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="px-3 py-1.5 text-[13px] rounded border border-warn-line text-warn hover:bg-warn-soft disabled:opacity-50"
          >
            {pending ? t('issueDetail.replying', 'Replying…') : t('issueDetail.reply', 'Reply')}
          </button>
        </div>
      )}
    </section>
  );
};

export default NeedsInputCard;
