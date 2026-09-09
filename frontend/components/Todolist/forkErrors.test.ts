import { describe, expect, it } from 'vitest';
import { RunForkRejectedError } from '../../services/aiLibraryService';
import { forkErrorText } from './forkErrors';

const t = (k: string, f: string) => `${k}|${f}`;

describe('forkErrorText', () => {
  it('maps every typed refusal code to its copy', () => {
    for (const code of ['run_live', 'issue_busy', 'issue_terminal', 'not_a_step_boundary', 'not_an_issue_run', 'run_state_unavailable', 'dispatch_failed', 'not_found']) {
      expect(forkErrorText(new RunForkRejectedError(code, 409, 'raw'), t)).toMatch(new RegExp(`^fork\\.error\\.`));
      expect(forkErrorText(new RunForkRejectedError(code, 409, 'raw'), t)).not.toContain('raw');
    }
  });
  it('falls back to the server message for an unknown code, and to generic copy for non-typed errors', () => {
    expect(forkErrorText(new RunForkRejectedError('weird', 500, 'server said so'), t)).toBe('server said so');
    expect(forkErrorText(new Error('boom'), t)).toBe('fork.error.generic|Could not fork the run');
  });
});
