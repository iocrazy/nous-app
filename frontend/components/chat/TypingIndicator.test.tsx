/**
 * TypingIndicator.test.tsx — RTL tests for all rendering branches.
 *
 * i18n: react-i18next is mocked so that t(key) → key, including interpolation args
 * (the mock ignores them). Assertions use i18n keys like 'chat.typing.one'.
 *
 * The component returns null when names is empty and show is falsy.
 * When show=true with empty names, it renders the 3 animated dot spans but no label.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TypingIndicator } from './TypingIndicator';

// Mock react-i18next: t(key, interpolationArgs) → key (args ignored)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string) => k,
  }),
}));

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TypingIndicator', () => {
  // ── Returns null ──────────────────────────────────────────────────────────

  it('renders nothing when names is empty and show is falsy (default)', () => {
    const { container } = render(<TypingIndicator />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when names=[] and show=false', () => {
    const { container } = render(<TypingIndicator names={[]} show={false} />);
    expect(container.firstChild).toBeNull();
  });

  // ── One user typing ────────────────────────────────────────────────────────

  it('renders chat.typing.one key when exactly one name is provided', () => {
    render(<TypingIndicator names={['Alice']} />);
    // With mock t, t('chat.typing.one', {name:'Alice'}) → 'chat.typing.one'
    expect(screen.getByText('chat.typing.one')).toBeDefined();
  });

  // ── Two users typing ───────────────────────────────────────────────────────

  it('renders chat.typing.two key when two names are provided', () => {
    render(<TypingIndicator names={['Alice', 'Bob']} />);
    // With mock t, t('chat.typing.two', {a:'Alice',b:'Bob'}) → 'chat.typing.two'
    expect(screen.getByText('chat.typing.two')).toBeDefined();
  });

  // ── Three or more users typing ─────────────────────────────────────────────

  it('renders chat.typing.many key when three or more names are provided', () => {
    render(<TypingIndicator names={['Alice', 'Bob', 'Carol']} />);
    // With mock t, t('chat.typing.many') → 'chat.typing.many'
    expect(screen.getByText('chat.typing.many')).toBeDefined();
  });

  it('renders chat.typing.many key for more than three names', () => {
    render(<TypingIndicator names={['A', 'B', 'C', 'D']} />);
    expect(screen.getByText('chat.typing.many')).toBeDefined();
  });

  // ── show=true with no names → bare dots, no label ─────────────────────────

  it('renders the animated dot spans with no label when show=true and names is empty', () => {
    const { container } = render(<TypingIndicator show />);
    // Component should render (not return null)
    expect(container.firstChild).not.toBeNull();
    // 3 dot spans should be present, no label span
    const spans = container.querySelectorAll('span');
    expect(spans).toHaveLength(3);
    // No label text keys should appear
    expect(screen.queryByText('chat.typing.one')).toBeNull();
    expect(screen.queryByText('chat.typing.two')).toBeNull();
    expect(screen.queryByText('chat.typing.many')).toBeNull();
  });

  it('renders the animated dot spans when show=true with empty names array', () => {
    const { container } = render(<TypingIndicator show names={[]} />);
    expect(container.firstChild).not.toBeNull();
    const spans = container.querySelectorAll('span');
    expect(spans).toHaveLength(3);
  });

  // ── show=true + names → dots AND label ────────────────────────────────────

  it('renders both dots and a label when show=true and names are provided', () => {
    const { container } = render(<TypingIndicator show names={['Alice']} />);
    expect(container.firstChild).not.toBeNull();
    // 3 dot spans + 1 label span = 4 spans
    const spans = container.querySelectorAll('span');
    expect(spans).toHaveLength(4);
    expect(screen.getByText('chat.typing.one')).toBeDefined();
  });
});
