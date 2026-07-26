import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptBadge } from './PromptBadge';
import type { Resource } from '../../types';

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
});
