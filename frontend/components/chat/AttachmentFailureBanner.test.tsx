/**
 * AttachmentFailureBanner.test.tsx — RTL tests for the attachment_failures
 * warn banner (Task 6, needs-input first-class plan).
 *
 * i18n: react-i18next is mocked so that t(key, {count}) → `${key}:${count}`
 * when interpolation args are passed, else just the key — mirrors the
 * pattern used by TypingIndicator.test.tsx but also asserts the count
 * actually reaches the translation call.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AttachmentFailureBanner } from './AttachmentFailureBanner';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: Record<string, unknown>) =>
      opts && 'count' in opts ? `${k}:${opts.count}` : k,
  }),
}));

describe('AttachmentFailureBanner', () => {
  it('renders nothing when count is undefined', () => {
    const { container } = render(<AttachmentFailureBanner count={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when count is 0', () => {
    const { container } = render(<AttachmentFailureBanner count={0} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the warn banner with the failure count interpolated', () => {
    render(<AttachmentFailureBanner count={2} />);
    expect(screen.getByText('chat.attachmentFailures:2')).toBeDefined();
    expect(screen.getByRole('status')).toBeDefined();
  });

  it('uses warn-semantic tokens, not legacy hue classes', () => {
    const { container } = render(<AttachmentFailureBanner count={1} />);
    const banner = container.firstChild as HTMLElement;
    expect(banner.className).toContain('border-warn-line');
    expect(banner.className).toContain('bg-warn-soft');
    expect(banner.className).not.toMatch(/amber|red|emerald|indigo/);
  });

  it('renders for a single failure with count=1', () => {
    render(<AttachmentFailureBanner count={1} />);
    expect(screen.getByText('chat.attachmentFailures:1')).toBeDefined();
  });
});
