/**
 * Copy for a refused CITATION (harness 3a Task 6) — same shape as
 * `laterErrors.ts` / `forkErrors.ts`: map the server's `code` to a sentence the
 * writer can act on, never print the server's own prose.
 *
 * The codes are the two `output_ref_resolver` actually raises
 * (`UNRESOLVABLE` / `LIMIT_EXCEEDED`), not invented ones. An unknown code still
 * names itself so a person can quote it in a bug report.
 *
 * Why the copy says the comment was NOT posted: citations are refused
 * all-or-nothing on purpose (the resolver's discipline #1). Every other
 * attachment kind degrades — twelve images with four unreadable still sends —
 * but a reference the server cannot resolve means the writer and the server
 * disagree about what was pointed at, and running the turn on that
 * disagreement is worse than refusing. The sentence has to carry that, or the
 * writer waits for a reply to a comment that never landed.
 */
import { IssueAnswerRejectedError } from '../../services/issueMessageService';
import { MAX_OUTPUT_REF_ATTACHMENTS } from '../chat/attachmentLimits';

type T = (key: string, fallback: string, vars?: Record<string, unknown>) => string;

const UNRESOLVABLE = 'output_ref_unresolvable';
const LIMIT_EXCEEDED = 'output_ref_limit_exceeded';

/** Does this rejection belong to the citation channel?
 *
 *  Exported so the caller's catch can branch rather than guess: another kind's
 *  typed refusal (a budget, a parked question) travels as the same error class
 *  and must reach its own handler. */
export function isOutputRefRejection(err: unknown): boolean {
  return (
    err instanceof IssueAnswerRejectedError
    && (err.code === UNRESOLVABLE || err.code === LIMIT_EXCEEDED)
  );
}

export function outputRefErrorText(err: unknown, t: T): string {
  const code = err instanceof IssueAnswerRejectedError ? err.code : '';
  if (code === UNRESOLVABLE) {
    return t(
      'outputs.refError.unresolvable',
      'One of the referenced outputs could not be read — it may have been revised or removed. Nothing was posted.',
    );
  }
  if (code === LIMIT_EXCEEDED) {
    // `{{n}}` rather than the digit: the cap is a mirror of a Python constant,
    // and a sentence with the number baked in keeps saying "8" after it moves.
    return t(
      'outputs.refError.limitExceeded',
      'A comment can reference at most {{n}} outputs. Nothing was posted.',
      { n: MAX_OUTPUT_REF_ATTACHMENTS },
    );
  }
  return t('outputs.refError.unknown', 'That reference was refused ({{code}}). Nothing was posted.', {
    code: code || 'unknown',
  });
}

/**
 * What the reply composer shows when a send fails.
 *
 * The branch, not the sentence, is the point. Before citations existed the
 * catch printed `err.message` unconditionally, which was right: the server's
 * message on that path was already a sentence for a person. A citation refusal
 * is not — it names a kind and a snowflake, for a log — and it is also the one
 * refusal where the WHOLE comment did not post, which the reader has to be
 * told or they will sit waiting for a reply to a message that never landed.
 *
 * Everything else keeps its own words: another kind's typed refusal already
 * has copy elsewhere, and dressing it as a citation problem would answer the
 * wrong question.
 */
export function replyErrorText(err: unknown, t: T): string {
  if (isOutputRefRejection(err)) return outputRefErrorText(err, t);
  if (err instanceof Error && err.message) return err.message;
  return t('issueDetail.sendFailed', 'Send failed');
}
