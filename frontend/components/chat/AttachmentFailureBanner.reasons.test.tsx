/**
 * The banner's REASON lines (P5 ruling C).
 *
 * The count alone was already an improvement on the old silent no-op, but "2
 * attachments could not be used" does not tell a user whether to re-share an
 * asset, generate its first image, or stop waiting on a deleted one. Four
 * typed reasons come back from the backend and each one has a different next
 * step.
 *
 * The i18n mock resolves against the REAL en locale rather than echoing keys:
 * an assertion that a key was addressed proves nothing about whether the
 * string exists, and a missing string is precisely the failure this feature
 * would otherwise ship with. `i18nParity.test.ts` covers zh separately.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import en from '../../public/locales/en.json';

const lookup = (key: string): string | undefined =>
  key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    en as unknown,
  ) as string | undefined;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: unknown) => {
      const hit = lookup(k);
      if (typeof hit === 'string') {
        if (opts && typeof opts === 'object' && 'count' in (opts as Record<string, unknown>)) {
          return hit.replace('{{count}}', String((opts as { count: unknown }).count));
        }
        return hit;
      }
      // The with-fallback form: `t(key, defaultString)`.
      return typeof opts === 'string' ? opts : k;
    },
  }),
}));

import { AttachmentFailureBanner } from './AttachmentFailureBanner';

const failure = (reason: string, index = 0) => ({ index, kind: 'asset_ref', reason });

describe('AttachmentFailureBanner — asset reasons', () => {
  it.each([
    ['asset_not_accessible', 'One Asset Is Not In A Workspace You Belong To'],
    ['asset_deleted', 'One Asset Has Been Deleted'],
    ['asset_no_primary_image', 'One Asset Has No Main Image Yet'],
    ['asset_type_unknown', 'One Asset Has A Type This Version Cannot Read'],
  ])('names %s in plain English', (reason, copy) => {
    render(<AttachmentFailureBanner count={1} failures={[failure(reason)]} />);
    expect(screen.getByText(copy)).toBeTruthy();
  });

  it('renders one line per DISTINCT reason, not one per failure', () => {
    render(
      <AttachmentFailureBanner
        count={3}
        failures={[failure('asset_deleted', 0), failure('asset_deleted', 1), failure('asset_deleted', 2)]}
      />,
    );
    expect(screen.getAllByTestId('attachment-failure-reason')).toHaveLength(1);
    // The count still reports all three — the lines summarise, they do not
    // replace the tally.
    expect(screen.getByText('3 attachment(s) failed to load')).toBeTruthy();
  });

  it('keeps two different reasons apart', () => {
    render(
      <AttachmentFailureBanner
        count={2}
        failures={[failure('asset_deleted', 0), failure('asset_no_primary_image', 1)]}
      />,
    );
    expect(
      screen.getAllByTestId('attachment-failure-reason').map((el) => el.getAttribute('data-reason')),
    ).toEqual(['asset_deleted', 'asset_no_primary_image']);
  });

  it('falls back to a generic line for a reason this build cannot name', () => {
    // Never the raw code: an untranslated identifier in a warn banner is the
    // failure mode this component exists to prevent, not a milder one.
    render(
      <AttachmentFailureBanner count={1} failures={[failure('some_future_reason')]} />,
    );
    const line = screen.getByTestId('attachment-failure-reason');
    expect(line.textContent).toBe('The Attachment Could Not Be Used');
    expect(line.textContent).not.toContain('some_future_reason');
  });

  it('still renders the headline alone when the caller kept no failures', () => {
    // The count and the reasons are independent inputs — a caller with only a
    // count is not a caller with no failures.
    render(<AttachmentFailureBanner count={2} />);
    expect(screen.getByText('2 attachment(s) failed to load')).toBeTruthy();
    expect(screen.queryByTestId('attachment-failure-reason')).toBeNull();
  });

  it('renders nothing when the count is zero, failures or not', () => {
    const { container } = render(
      <AttachmentFailureBanner count={0} failures={[failure('asset_deleted')]} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
