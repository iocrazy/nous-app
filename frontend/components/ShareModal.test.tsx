/**
 * ShareModal was the app's ONLY caller of `components/DateTimePicker.tsx` — a
 * second date/time implementation whose month and weekday names were hardcoded
 * English, so it could never render Chinese no matter what the locale files
 * said. This file pins that the expiration field now goes through the shared
 * `DateTimePopover` like every other date control, and that the deleted
 * component has not crept back in.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/sharesService', () => ({
  createShare: vi.fn().mockResolvedValue({ id: 's1', token: 'tok' }),
}));

import { ShareModal } from './ShareModal';

afterEach(cleanup);

describe('ShareModal expiration field', () => {
  const open = () =>
    render(<ShareModal isOpen onClose={vi.fn()} resourceId="r1" defaultName="Clip" />);

  it('opens the shared DateTimePopover with a clock, not a bespoke picker', () => {
    open();
    // Expiration is off by default — the picker only exists once it is armed.
    expect(screen.queryByTestId('share-expiration-trigger')).toBeNull();

    fireEvent.click(screen.getByRole('switch', { name: /Expiration/i }));
    const trigger = screen.getByTestId('share-expiration-trigger');
    expect(trigger).toHaveTextContent('Select date and time');

    fireEvent.click(trigger);
    expect(screen.getByTestId('date-time-popover')).toBeInTheDocument();
    expect(screen.getByTestId('date-time-columns')).toBeInTheDocument();
  });

  it('cannot select a day in the past — a share that has already expired is not a share', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 15, 10, 0, 0));
    try {
      open();
      fireEvent.click(screen.getByRole('switch', { name: /Expiration/i }));
      fireEvent.click(screen.getByTestId('share-expiration-trigger'));

      expect(screen.getByLabelText('2026-07-14')).toBeDisabled();
      expect(screen.getByLabelText('2026-07-16')).not.toBeDisabled();

      fireEvent.click(screen.getByLabelText('2026-07-16'));
      fireEvent.click(screen.getByTestId('date-time-hour-18'));
      // The stored shape is the local wall clock the API already expects.
      expect(screen.getByTestId('share-expiration-trigger')).toHaveTextContent(/2026-07-16 18:/);
    } finally {
      vi.useRealTimers();
    }
  });
});
