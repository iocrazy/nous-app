import React, { useEffect, useState } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { SMS_CODE_PATTERN } from '../../services/distributionService';
import { usePublishSmsChallenge } from './usePublishSmsChallenge';

/**
 * The input the user types a verification code into when a publish stops to ask.
 *
 * Before this existed, a platform that demanded a code mid-publish simply lost
 * the post: the browser had no channel to receive one and failed with a message
 * saying exactly that. The channel now exists end to end, and this is its last
 * hop — the point where "the platform is asking" becomes something a person can
 * see and answer.
 *
 * --- what it renders, and when it stays quiet -------------------------------
 *
 * It renders **only** on `waiting`, and on `ended` long enough to say how the
 * challenge finished. `checking`, `unavailable` and `quiet` all render nothing.
 *
 * That is three states collapsing to the same *output* but never to the same
 * *claim*: nothing here ever tells the user "this publish needs no code",
 * because two of those three states do not know that. `PublishPage`'s
 * `imagesGate` makes the opposite trade — it folds "loading" and "request
 * failed" into an assertion about the platform — and that is the mistake this
 * component is written not to repeat.
 */
export const PublishSmsPrompt: React.FC<{
  taskId: number | string;
  /** Whether this batch is running. Polling is pointless once it is not. */
  active: boolean;
}> = ({ taskId, active }) => {
  const { t } = useTranslation();
  const { phase, verdict, submitting, submit, clearVerdict } = usePublishSmsChallenge(taskId, active);
  const [code, setCode] = useState('');

  // A fresh challenge must not inherit the previous one's refusal notice.
  useEffect(() => {
    if (phase.kind === 'waiting' && verdict && !verdict.retryable) clearVerdict();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.kind]);

  if (phase.kind !== 'waiting' && phase.kind !== 'ended') return null;

  if (phase.kind === 'ended') {
    // Only outcomes the user needs to act on get a line. `accepted` needs none:
    // the publish carried on, and the batch's own row already says so.
    const endedCopy: Record<string, [string, string]> = {
      expired: ['distribution.publishSms.endedExpired', 'No code was entered in time, so this publish was stopped. Retry the batch to try again.'],
      exhausted: ['distribution.publishSms.endedExhausted', 'The platform refused every code that was entered. Retry the batch to try again.'],
      abandoned: ['distribution.publishSms.endedAbandoned', 'This publish stopped waiting for a code.'],
    };
    const entry = endedCopy[phase.outcome];
    if (!entry) return null;
    return (
      <div className="mt-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs text-warn">
        {t(entry[0], entry[1])}
      </div>
    );
  }

  const valid = SMS_CODE_PATTERN.test(code);
  // `retryable` comes from the browser and is never re-derived here: deriving it
  // twice is two chances to disagree with the process that actually owns the
  // answer, and the disagreement would read "you can try again" next to a
  // publish that already gave up.
  const refused = verdict?.retryable === true;

  const refusalCopy = (): string => {
    if (!verdict) return '';
    if (verdict.outcome === 'unreachable') {
      return t(
        'distribution.publishSms.unreachable',
        'That code could not be delivered — this is our side, not yours. Try sending it again.',
      );
    }
    return t(
      'distribution.publishSms.rejected',
      'That code was not accepted and the platform is still asking. Check it and send it again.',
    );
  };

  const onSubmit = async () => {
    if (!valid || submitting) return;
    const result = await submit(code);
    // Deliberately NOT clearing the field on a refusal: a refused code is almost
    // always one mistyped digit, and wiping it sends the user back to their SMS
    // app to re-read all of them for the sake of one.
    if (result && !result.retryable) setCode('');
  };

  return (
    <div
      className="mt-2 rounded-md border border-warn/50 bg-warn/5 px-3 py-3"
      role="group"
      aria-label={t('distribution.publishSms.title', 'Verification code required')}
    >
      <div className="flex items-center gap-2 text-xs font-medium text-warn">
        <KeyRound size={14} aria-hidden="true" />
        <span>{t('distribution.publishSms.title', 'Verification code required')}</span>
      </div>
      <p className="mt-1 text-xs text-muted">
        {t(
          'distribution.publishSms.body',
          'The platform asked for a verification code before it will publish. Enter the code it sent to this account.',
        )}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="text"
          value={code}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          aria-invalid={code.length > 0 && !valid}
          aria-label={t('distribution.publishSms.inputLabel', 'Verification code')}
          placeholder={t('distribution.publishSms.placeholder', '4-8 digit code')}
          // Strip anything the server would 422 on as it is typed, paste included.
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
          disabled={submitting}
          onKeyDown={(e) => { if (e.key === 'Enter') void onSubmit(); }}
          className="w-32 rounded border border-line bg-surface px-2 py-1 text-sm"
        />
        <button
          type="button"
          onClick={() => { void onSubmit(); }}
          disabled={!valid || submitting}
          className="inline-flex items-center gap-1 rounded bg-warn px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
        >
          {submitting && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
          {t('distribution.publishSms.submit', 'Submit')}
        </button>
        <span className="text-[11px] text-muted">
          {t('distribution.publishSms.attemptsLeft', '{{count}} attempt(s) left', {
            count: phase.attemptsLeft,
          })}
        </span>
      </div>
      {refused && (
        <p className="mt-2 text-xs text-danger" role="alert">
          {refusalCopy()}
        </p>
      )}
    </div>
  );
};

export default PublishSmsPrompt;
