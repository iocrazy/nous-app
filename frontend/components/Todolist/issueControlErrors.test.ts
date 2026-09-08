import { describe, expect, it } from 'vitest';
import { IssueControlError } from '../../services/issuesService';
import { controlErrorText } from './issueControlErrors';

const t = (key: string, fallback: string) => `${key}|${fallback}`;

describe('controlErrorText', () => {
  it('maps known codes to their i18n key', () => {
    expect(controlErrorText(new IssueControlError('already_paused', 409, 'x'), t)).toBe('issueDetail.controlError.already_paused|Already paused');
    expect(controlErrorText(new IssueControlError('run_state_unavailable', 503, 'x'), t)).toContain('issueDetail.controlError.run_state_unavailable|');
    expect(controlErrorText(new IssueControlError('http_403', 403, 'x'), t)).toBe('issueDetail.controlError.forbidden|You cannot control this issue');
  });

  it('falls back to the server message for an unknown code, never the raw body', () => {
    expect(controlErrorText(new IssueControlError('budget_still_exhausted', 409, 'budget still exhausted'), t)).toBe('budget still exhausted');
    expect(controlErrorText(new IssueControlError('http_500', 500, ''), t)).toBe('issueDetail.controlError.generic|Could not update the issue');
  });

  it('says generic for a non-control error (network, TypeError)', () => {
    expect(controlErrorText(new TypeError('Failed to fetch'), t)).toBe('issueDetail.controlError.generic|Could not update the issue');
  });
});
