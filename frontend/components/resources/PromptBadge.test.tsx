import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptBadge } from './PromptBadge';
import type { Resource } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const res = (over: Partial<Resource> = {}): Resource =>
  ({ id: 'r1', gen_prompt: 'masterpiece, 1girl', gen_prompt_negative: 'lowres', ...over }) as unknown as Resource;

describe('PromptBadge', () => {
  it('renders nothing without prompt data', () => {
    const { container } = render(<PromptBadge resource={res({ gen_prompt: null, gen_prompt_negative: null })} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders badge when prompt exists', () => {
    render(<PromptBadge resource={res()} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
  });

  it('hover reveals popover with positive and negative preview', () => {
    render(<PromptBadge resource={res()} />);
    fireEvent.mouseEnter(screen.getByText('Prompt'));
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.getByText(/lowres/)).toBeTruthy();
  });

  it('popover anchors left when shiftLeft is true', () => {
    const { container } = render(
      <PromptBadge resource={res()} shiftLeft={true} />
    );
    fireEvent.mouseEnter(screen.getByText('Prompt'));
    // Find the popover container div
    const popoverDiv = container.querySelector('.w-40.bg-ink-950');
    expect(popoverDiv?.className).toMatch(/left-0/);
    expect(popoverDiv?.className).not.toMatch(/right-0/);
  });

  it('negative-only resource does not render empty positive paragraph', () => {
    const { container } = render(
      <PromptBadge resource={res({ gen_prompt: null, gen_prompt_negative: 'lowres' })} />
    );
    fireEvent.mouseEnter(screen.getByText('Prompt'));
    // Should show negative text
    expect(screen.getByText(/lowres/)).toBeTruthy();
    // Should NOT render an empty positive paragraph
    const positiveParagraph = container.querySelector('p.line-clamp-3');
    expect(positiveParagraph).toBeNull();
  });
});
