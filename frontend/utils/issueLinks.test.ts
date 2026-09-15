/**
 * B7 — the frontend issue-link builder, held to the backend one's contract.
 *
 * The expected strings here are copied from
 * `backend/app/services/issues/issue_links.py` and its docstring example
 * (`"/team/424242424242/todolist/MH-91?step=3&turn=2"`), so a change on one
 * side that is not made on the other shows up as a diff in an assertion
 * rather than as a link that quietly lands in the wrong place.
 */
import { describe, expect, it } from 'vitest';

import { issueDeepLink } from './issueLinks';

describe('issueDeepLink', () => {
  it('builds the team-scoped todolist route', () => {
    expect(issueDeepLink('424242424242', 'MH-91')).toBe('/team/424242424242/todolist/MH-91');
  });

  it('formats a numeric team id the same as a string one', () => {
    // `issues.team_id` is a native number in some responses and a string in
    // others; both must format to one URL (the backend builder says the same).
    expect(issueDeepLink(424242424242, 'MH-91')).toBe(issueDeepLink('424242424242', 'MH-91'));
  });

  it('carries a step, and a turn only alongside one', () => {
    expect(issueDeepLink('424242424242', 'MH-91', { step: 3, turn: 2 })).toBe(
      '/team/424242424242/todolist/MH-91?step=3&turn=2',
    );
    expect(issueDeepLink('424242424242', 'MH-91', { turn: 2 })).toBe(
      '/team/424242424242/todolist/MH-91',
    );
  });

  it('keeps step 0 — steps are 0-based, so truth-testing would drop the first one', () => {
    expect(issueDeepLink('42', 'MH-1', { step: 0, turn: 0 })).toBe('/team/42/todolist/MH-1?step=0&turn=0');
  });

  it('refuses without a team or without a key — falsy counts, as Python does', () => {
    // There is no team-less route that takes an identifier: `router.tsx` has
    // `todolist/:identifier` under `/team/:teamId` only.
    //
    // `0` is in here because the backend guard is `if not team_id` (L1): a
    // team id of 0 is not a team, and a TS side that only checked for
    // null/undefined would build `/team/0/todolist/MH-1` where Python
    // refuses. `test_issue_links_frontend_mirror.py` pins the same pair.
    expect(issueDeepLink(undefined, 'MH-91')).toBeNull();
    expect(issueDeepLink(null, 'MH-91')).toBeNull();
    expect(issueDeepLink('', 'MH-91')).toBeNull();
    expect(issueDeepLink(0, 'MH-91')).toBeNull();
    expect(issueDeepLink('42', null)).toBeNull();
    expect(issueDeepLink('42', '')).toBeNull();
  });
});
