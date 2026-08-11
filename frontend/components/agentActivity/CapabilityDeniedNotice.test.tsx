/**
 * CapabilityDeniedNotice — presentational two-state test (interactive vs not).
 *
 * i18n is mocked to echo interpolation options into the returned string so
 * assertions can check the tool name landed in the rendered text without
 * depending on the real locale files (same spirit as the mock in
 * TurnWriteSummary.test.tsx, generalised to any interpolation key).
 */

import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { CapabilityDeniedNotice } from './CapabilityDeniedNotice';
import type { CapabilityDenial } from './toolActivity';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) => {
      if (typeof arg2 === 'string') return arg2; // fallback default string
      if (arg2 && typeof arg2 === 'object') {
        return `${key}:${Object.values(arg2 as Record<string, unknown>).join(',')}`;
      }
      return key;
    },
  }),
}));

const denials: CapabilityDenial[] = [
  { key: 'seq:1', tool: 'CreateShot', reason: 'write_level=none blocks CreateShot' },
];

describe('CapabilityDeniedNotice', () => {
  it('renders nothing when there are no denials', () => {
    const { container } = render(
      <MemoryRouter>
        <CapabilityDeniedNotice denials={[]} interactive />
      </MemoryRouter>,
    );
    expect(container.querySelector('[data-testid="capability-denied-notice"]')).toBeNull();
  });

  it('interactive: renders the tool + raw reason and a CTA link to AI Library settings', () => {
    const { container } = render(
      <MemoryRouter>
        <CapabilityDeniedNotice denials={denials} interactive />
      </MemoryRouter>,
    );
    const notice = container.querySelector('[data-testid="capability-denied-notice"]');
    expect(notice).not.toBeNull();
    expect(notice!.textContent).toContain('CreateShot');
    // reason renders verbatim — it is the backend's English abort_reason,
    // never passed through t().
    expect(notice!.textContent).toContain('write_level=none blocks CreateShot');

    const cta = container.querySelector('a[href="/settings?tab=ai"]');
    expect(cta).not.toBeNull();
  });

  it('non-interactive (issue timeline): plain text, no CTA', () => {
    const { container } = render(
      <CapabilityDeniedNotice denials={denials} interactive={false} />,
    );
    expect(
      container.querySelector('[data-testid="capability-denied-notice"]'),
    ).not.toBeNull();
    expect(container.querySelector('a')).toBeNull();
  });
});
