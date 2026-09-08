/**
 * Copy for a failed pause / resume (phase 2a §2). The service throws a typed
 * IssueControlError; this maps its `code` to a sentence the user can act on.
 * Unknown codes fall back to the server message (never the raw HTTP body —
 * `_controlJson` already stripped that).
 */
import { IssueControlError } from '../../services/issuesService';

type T = (key: string, fallback: string) => string;

const COPY: Record<string, [key: string, fallback: string]> = {
  already_paused: ['issueDetail.controlError.already_paused', 'Already paused'],
  not_paused: ['issueDetail.controlError.not_paused', 'Not paused any more'],
  run_state_unavailable: ['issueDetail.controlError.run_state_unavailable', 'Run state unavailable — try again in a moment'],
  http_403: ['issueDetail.controlError.forbidden', 'You cannot control this issue'],
  http_404: ['issueDetail.controlError.not_found', 'Issue not found'],
};

export function controlErrorText(err: unknown, t: T): string {
  if (err instanceof IssueControlError) {
    const hit = COPY[err.code];
    if (hit) return t(hit[0], hit[1]);
    return err.message || t('issueDetail.controlError.generic', 'Could not update the issue');
  }
  // Network / unexpected: say so, do not leak the stack.
  return t('issueDetail.controlError.generic', 'Could not update the issue');
}
