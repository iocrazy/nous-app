/**
 * LogsPanel — the "Custom range" filter is the shared DateTimePopover, not two
 * native `<input type="date">` fields.
 *
 * The pin that matters is equivalence, not the widget: `customStartDate` /
 * `customEndDate` must still be plain 'YYYY-MM-DD' strings and must still ride
 * the wire as `start_date` / `end_date` query params. The popover commits both
 * ends in ONE onChange (commit-on-complete), where the two inputs committed
 * independently — so this asserts the completed range reaches the request.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));

vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => null,
  isSupabaseConfigured: () => false,
}));

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'http://api.test',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));

import { LogsPanel } from './LogsPanel';

const EMPTY_RESPONSE = {
  success: true,
  logs: [],
  total: 0,
  page: 1,
  page_size: 50,
  total_pages: 1,
};

function lastRequestUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const calls = fetchMock.mock.calls;
  return String(calls[calls.length - 1][0]);
}

describe('LogsPanel — custom date range uses DateTimePopover', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 7, 13, 10, 0, 0)); // local 2026-08-13
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => EMPTY_RESPONSE,
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  async function openCustomRange() {
    render(<LogsPanel />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.change(screen.getByDisplayValue('Last 7 days'), {
      target: { value: 'custom' },
    });
    const trigger = await screen.findByTestId('logs-custom-range-trigger');
    fireEvent.click(trigger);
    return trigger;
  }

  it('the trigger opens the popover and a completed range lands in both ends', async () => {
    const trigger = await openCustomRange();
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('2026-08-05'));
    // First click only buffers a draft — nothing committed yet.
    expect(trigger.textContent).toContain('Select date range');

    fireEvent.click(screen.getByLabelText('2026-08-12'));

    expect(trigger.textContent).toContain('2026-08-05');
    expect(trigger.textContent).toContain('2026-08-12');

    await waitFor(() => {
      const url = lastRequestUrl(fetchMock);
      expect(url).toContain('start_date=2026-08-05');
      expect(url).toContain('end_date=2026-08-12');
    });
  });

  it('Clear empties both ends', async () => {
    const trigger = await openCustomRange();
    fireEvent.click(screen.getByLabelText('2026-08-05'));
    fireEvent.click(screen.getByLabelText('2026-08-12'));
    await waitFor(() => expect(lastRequestUrl(fetchMock)).toContain('start_date'));

    fireEvent.click(screen.getByTestId('date-time-clear'));

    expect(trigger.textContent).toContain('Select date range');
    await waitFor(() => {
      const url = lastRequestUrl(fetchMock);
      expect(url).not.toContain('start_date');
      expect(url).not.toContain('end_date');
    });
  });
});
