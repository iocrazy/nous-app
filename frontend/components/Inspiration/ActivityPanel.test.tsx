import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getActivity = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  getActivity: (...a: unknown[]) => getActivity(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { ActivityPanel } from './ActivityPanel';

describe('ActivityPanel', () => {
  beforeEach(() => {
    // Module-level `getActivity` mock is shared across every `it` in this
    // suite; the global `afterEach` in tests/setup.ts only restores spies
    // created with vi.spyOn, so a plain vi.fn()'s call history survives
    // between tests unless cleared explicitly here (same pattern as
    // components/Todolist/IssueReplyBox.test.tsx).
    vi.clearAllMocks();
    localStorage.clear();
    getActivity.mockResolvedValue([{ day: '2026-07-07', cnt: 3 }]);
  });

  it('loads ~16 weeks of activity on mount', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    const [from, to] = getActivity.mock.calls[0];
    const days = (new Date(to).getTime() - new Date(from).getTime()) / 86400000;
    expect(days).toBeGreaterThanOrEqual(105);
    expect(days).toBeLessThanOrEqual(120);
  });

  it('clicking a heatmap cell selects that date; clicking again clears', async () => {
    const onSelectDate = vi.fn();
    const { rerender } = render(
      <ActivityPanel selectedDate={null} onSelectDate={onSelectDate} refreshKey={0} />,
    );
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    const cell = await screen.findByLabelText('2026-07-07: 3 notes');
    fireEvent.click(cell);
    expect(onSelectDate).toHaveBeenCalledWith('2026-07-07');
    rerender(<ActivityPanel selectedDate="2026-07-07" onSelectDate={onSelectDate} refreshKey={0} />);
    fireEvent.click(screen.getByLabelText('2026-07-07: 3 notes'));
    expect(onSelectDate).toHaveBeenLastCalledWith(null);
  });

  it('mode toggle switches to calendar and persists', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText('Calendar view'));
    expect(localStorage.getItem('inspiration.activityMode')).toBe('calendar');
    expect(screen.getByLabelText('Previous month')).toBeTruthy();
  });

  it('refreshKey change refetches', async () => {
    const { rerender } = render(
      <ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />,
    );
    await waitFor(() => expect(getActivity).toHaveBeenCalledTimes(1));
    rerender(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={1} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalledTimes(2));
  });
});
