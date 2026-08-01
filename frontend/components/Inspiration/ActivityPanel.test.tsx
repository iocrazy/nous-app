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

describe('ActivityPanel calendar enrichment (go-live feedback)', () => {
  // Calendar view opens on the CURRENT month, so the seeded note must live in
  // it — a hardcoded '2026-07-07' made these two tests fail on every CI run
  // where the UTC month had moved on (first seen 2026-08-01T00:33Z, while
  // local dev machines still on 07-31 kept passing).
  const now = new Date();
  const seedDay = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-07`;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('inspiration.activityMode', 'calendar');
    getActivity.mockResolvedValue([{ day: seedDay, cnt: 3 }]);
  });

  it('days with notes show a visible count badge', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    const cell = await screen.findByLabelText(`${seedDay}: 3 notes`);
    expect(cell.textContent).toContain('3');
  });

  it('Today button selects today and shows the month footer total', async () => {
    const onSelectDate = vi.fn();
    render(<ActivityPanel selectedDate={null} onSelectDate={onSelectDate} refreshKey={0} />);
    await screen.findByLabelText(`${seedDay}: 3 notes`);
    fireEvent.click(screen.getByText('Today'));
    expect(onSelectDate).toHaveBeenCalledWith(expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/));
    expect(screen.getByText(/notes this month/)).toBeTruthy();
  });

  it('selected date shows an in-panel clear chip', async () => {
    const onSelectDate = vi.fn();
    render(<ActivityPanel selectedDate="2026-07-07" onSelectDate={onSelectDate} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Clear selected day'));
    expect(onSelectDate).toHaveBeenCalledWith(null);
  });
});

describe('ActivityPanel year/month wheel picker', () => {
  const nowYear = new Date().getFullYear();

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('inspiration.activityMode', 'calendar');
    getActivity.mockResolvedValue([]);
  });

  it('clicking the month label opens a floating popover with the year wheel + months', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    expect(screen.getByRole('dialog', { name: 'Year and month picker' })).toBeTruthy();
    // Wheel centers on the current year with two past years above it.
    expect(screen.getByLabelText(`Year ${nowYear}`)).toBeTruthy();
    expect(screen.getByLabelText(`Year ${nowYear - 1}`)).toBeTruthy();
    expect(screen.getByText('Jan')).toBeTruthy();
    expect(screen.getByText('Dec')).toBeTruthy();
  });

  it('opening the picker lazily fetches full-year activity for the browsed year', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    await waitFor(() =>
      expect(getActivity).toHaveBeenCalledWith(`${nowYear}-01-01`, `${nowYear}-12-31`),
    );
  });

  it('clicking a year in the wheel recenters it and fetches that year', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    fireEvent.click(screen.getByLabelText(`Year ${nowYear - 2}`));
    // Recentered: a year two below the new center (still <= now) is now visible.
    expect(screen.getByLabelText(`Year ${nowYear - 4}`)).toBeTruthy();
    await waitFor(() =>
      expect(getActivity).toHaveBeenCalledWith(`${nowYear - 2}-01-01`, `${nowYear - 2}-12-31`),
    );
  });

  it('mouse wheel steps the year into the past and reveals older years', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    // Before scrolling, nowYear-3 is out of the 5-slot window.
    expect(screen.queryByLabelText(`Year ${nowYear - 3}`)).toBeNull();
    const wheel = screen.getByLabelText(`Year ${nowYear}`).closest('div')!.parentElement!;
    fireEvent.wheel(wheel, { deltaY: -1 }); // up = into the past
    expect(screen.getByLabelText(`Year ${nowYear - 3}`)).toBeTruthy();
  });

  it('never renders a year beyond the current real year (future cap)', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    const wheel = screen.getByLabelText(`Year ${nowYear}`).closest('div')!.parentElement!;
    fireEvent.wheel(wheel, { deltaY: 1 }); // down = toward now, but already capped
    expect(screen.queryByLabelText(`Year ${nowYear + 1}`)).toBeNull();
    expect(screen.getByLabelText(`Year ${nowYear}`)).toBeTruthy();
  });

  it('picking a month in another year jumps the calendar there and closes', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    fireEvent.click(screen.getByLabelText(`Year ${nowYear - 1}`));
    fireEvent.click(screen.getByText('Mar'));
    expect(screen.getByLabelText('Choose year and month').textContent).toContain(
      `March ${nowYear - 1}`,
    );
    expect(screen.queryByRole('dialog')).toBeNull(); // popover closed
  });

  it('Escape closes the popover', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('mousedown outside the popover closes it', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.mouseDown(screen.getByText('Activity'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Back to this month recenters + selects the current month and closes', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    // Browse away first.
    fireEvent.click(screen.getByLabelText(`Year ${nowYear - 2}`));
    fireEvent.click(screen.getByText('Back to this month'));
    expect(screen.queryByRole('dialog')).toBeNull();
    const now = new Date().toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    expect(screen.getByLabelText('Choose year and month').textContent).toContain(now);
  });

  it('Today closes the picker and returns to the current month', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    fireEvent.click(await screen.findByLabelText('Choose year and month'));
    fireEvent.click(screen.getByText('Today'));
    expect(screen.queryByRole('dialog')).toBeNull();
    const now = new Date().toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    expect(screen.getByLabelText('Choose year and month').textContent).toContain(now);
  });
});
