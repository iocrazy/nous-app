/**
 * Copy for a refused fork (harness 2b-1 §2). The service throws a typed
 * RunForkRejectedError; this maps its `code` to a sentence the user can act
 * on. Unknown codes fall back to the server message (never the raw body —
 * `forkJson` already stripped that).
 */
import { RunForkRejectedError } from '../../services/aiLibraryService';

type T = (key: string, fallback: string) => string;

const COPY: Record<string, [key: string, fallback: string]> = {
  run_live: ['fork.error.run_live', 'The run is still going — pause or cancel it first'],
  issue_busy: ['fork.error.issue_busy', 'The issue is still being worked on — wait for it to settle'],
  issue_terminal: ['fork.error.issue_terminal', 'The issue is closed — reopen it before forking'],
  not_a_step_boundary: ['fork.error.not_a_step_boundary', 'Pick a step boundary to fork from'],
  not_an_issue_run: ['fork.error.not_an_issue_run', 'Only issue runs can be forked'],
  run_state_unavailable: ['fork.error.run_state_unavailable', 'Run state unavailable — try again in a moment'],
  dispatch_failed: ['fork.error.dispatch_failed', 'Could not start the forked run — the issue was left as it was'],
  http_404: ['fork.error.not_found', 'Run not found'],
  not_found: ['fork.error.not_found', 'Run not found'],
};

export function forkErrorText(err: unknown, t: T): string {
  if (err instanceof RunForkRejectedError) {
    const hit = COPY[err.code];
    if (hit) return t(hit[0], hit[1]);
    return err.message || t('fork.error.generic', 'Could not fork the run');
  }
  return t('fork.error.generic', 'Could not fork the run');
}
