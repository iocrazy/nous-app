/**
 * SubtaskBar — renders the done/total progress, hides on zero children, and
 * goes emerald once every child is terminal.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SubtaskBar } from './SubtaskBar';

describe('SubtaskBar', () => {
  it('shows done/total for a partially-complete set', () => {
    render(<SubtaskBar count={{ done: 2, total: 5 }} />);
    const bar = screen.getByTestId('subtask-bar');
    expect(bar).toHaveTextContent('2/5');
    expect(bar.querySelector('.text-emerald-400')).toBeNull();
  });

  it('renders nothing when there are no children (no 0/0)', () => {
    render(<SubtaskBar count={{ done: 0, total: 0 }} />);
    expect(screen.queryByTestId('subtask-bar')).toBeNull();
  });

  it('turns emerald when every child is done', () => {
    render(<SubtaskBar count={{ done: 4, total: 4 }} />);
    const bar = screen.getByTestId('subtask-bar');
    expect(bar).toHaveTextContent('4/4');
    // the count label carries the emerald "complete" tint
    expect(bar.querySelector('.text-emerald-400')).not.toBeNull();
  });
});
