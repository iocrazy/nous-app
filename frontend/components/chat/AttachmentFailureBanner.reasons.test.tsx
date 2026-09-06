/**
 * The banner's REASON lines (P5 ruling C).
 *
 * The count alone was already an improvement on the old silent no-op, but "2
 * attachments could not be used" does not tell a user whether to re-share an
 * asset, generate its first image, or stop waiting on a deleted one. Five
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
    // The real three-arg shape: `t(key)`, `t(key, options)` and
    // `t(key, defaultValue, options)` are all live in this component, and a
    // two-arg stand-in silently drops the interpolation values of the third
    // form — the banner would render "{{n}} Attachments…" and the test would
    // be measuring the mock rather than the component.
    t: (k: string, arg2?: unknown, arg3?: unknown) => {
      const opts = (typeof arg2 === 'object' ? arg2 : arg3) as
        | Record<string, unknown>
        | undefined;
      const hit = lookup(k);
      const template =
        typeof hit === 'string' ? hit : typeof arg2 === 'string' ? arg2 : k;
      let out = template;
      for (const [name, value] of Object.entries(opts ?? {})) {
        out = out.split(`{{${name}}}`).join(String(value));
      }
      return out;
    },
  }),
}));

import { AttachmentFailureBanner } from './AttachmentFailureBanner';
import { MAX_ASSET_REF_ATTACHMENTS } from './attachmentLimits';

const failure = (reason: string, index = 0) => ({ index, kind: 'asset_ref', reason });

/** A BINARY-path failure. `chat_attachment_resolver` builds these as
 *  `f"{type(exc).__name__}: {exc}"`, so the reason is a free-form exception
 *  string — not a code, not translatable, and almost never repeated verbatim. */
const binaryFailure = (reason: string, index = 0) => ({ index, kind: 'image', reason });

describe('AttachmentFailureBanner — asset reasons', () => {
  it.each([
    ['asset_not_accessible', 'One Asset Is Not In A Workspace You Belong To'],
    ['asset_deleted', 'One Asset Has Been Deleted'],
    ['asset_no_primary_image', 'One Asset Has No Main Image Yet'],
    ['asset_type_unknown', 'One Asset Has A Type This Version Cannot Read'],
    // v2's sixth code. The loadout picker on the staged chip made a foreign
    // loadout id reachable by a real user, so the backend stopped falling back
    // to the default and started refusing out loud.
    ['loadout_not_owned', 'That Loadout Does Not Belong To This Character'],
    // Interpolated, not literal: the copy says `{{n}}` and the banner feeds it
    // `MAX_ASSET_REF_ATTACHMENTS`. Asserting the rendered sentence is what
    // proves the interpolation actually happened — a raw `{{n}}` reaching a
    // user is exactly the class of bug this file exists to catch.
    ['attachment_limit_exceeded', `Only The First ${MAX_ASSET_REF_ATTACHMENTS} Assets Were Used`],
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

  it('never leaves a raw interpolation token in the rendered copy', () => {
    // The failure mode the constant exists to prevent, stated directly: if the
    // banner stopped passing `n`, i18next would render `{{n}}` verbatim in a
    // user-facing warning.
    render(
      <AttachmentFailureBanner
        count={1}
        failures={[failure('attachment_limit_exceeded', 8)]}
      />,
    );
    const line = screen.getByTestId('attachment-failure-reason');
    expect(line.textContent).not.toContain('{{');
    expect(
      screen.getByText(`Only The First ${MAX_ASSET_REF_ATTACHMENTS} Assets Were Used`),
    ).toBeTruthy();
    expect(line.getAttribute('data-reason')).toBe('attachment_limit_exceeded');
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
});

describe('AttachmentFailureBanner — free-form binary reasons are counted, not listed', () => {
  it('collapses several unnamed failures into ONE line carrying the count', () => {
    // Before this, three binary failures printed the same generic sentence
    // three times — a line that says nothing, said repeatedly.
    render(
      <AttachmentFailureBanner
        count={3}
        failures={[
          binaryFailure('ValueError: bad header', 0),
          binaryFailure('TimeoutError: read timed out', 1),
          binaryFailure('produced no usable attachments', 2),
        ]}
      />,
    );
    const lines = screen.getAllByTestId('attachment-failure-reason');
    expect(lines).toHaveLength(1);
    expect(lines[0].getAttribute('data-unnamed-count')).toBe('3');
    expect(lines[0].textContent).toBe('3 Attachments Could Not Be Used');
  });

  it('uses the singular line for exactly one unnamed failure', () => {
    render(
      <AttachmentFailureBanner count={1} failures={[binaryFailure('ValueError: bad header')]} />,
    );
    expect(screen.getByTestId('attachment-failure-reason').textContent).toBe(
      'The Attachment Could Not Be Used',
    );
  });

  it('never prints the exception text at the user', () => {
    render(
      <AttachmentFailureBanner
        count={2}
        failures={[binaryFailure('ValueError: bad header', 0), binaryFailure('OSError: x', 1)]}
      />,
    );
    const banner = screen.getByRole('status');
    expect(banner.textContent).not.toContain('ValueError');
    expect(banner.textContent).not.toContain('OSError');
  });

  it('keeps the typed asset lines beside the counted generic one', () => {
    // A mixed turn: an asset failed for a reason worth naming, and two files
    // failed for reasons that are not. Both halves have to survive.
    render(
      <AttachmentFailureBanner
        count={3}
        failures={[
          failure('asset_deleted', 0),
          binaryFailure('ValueError: bad header', 1),
          binaryFailure('OSError: x', 2),
        ]}
      />,
    );
    const lines = screen.getAllByTestId('attachment-failure-reason');
    expect(lines.map((el) => el.getAttribute('data-reason'))).toEqual([
      'asset_deleted',
      'unknown',
    ]);
    expect(lines[0].textContent).toBe('One Asset Has Been Deleted');
    expect(lines[1].textContent).toBe('2 Attachments Could Not Be Used');
  });

  it('counts every unnamed failure, including repeats of one string', () => {
    // De-duplicating the generic bucket would UNDER-report: two files that
    // failed identically are still two files the user did not get.
    render(
      <AttachmentFailureBanner
        count={2}
        failures={[binaryFailure('OSError: x', 0), binaryFailure('OSError: x', 1)]}
      />,
    );
    expect(
      screen.getByTestId('attachment-failure-reason').getAttribute('data-unnamed-count'),
    ).toBe('2');
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
