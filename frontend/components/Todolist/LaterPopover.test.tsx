/**
 * harness 2b-2 §5-2 — the "⏰ Later" popover.
 *
 * The POST body is asserted against the REAL wire shape: `issue_id` is a JSON
 * NUMBER (issue ids are unconverted BIGINTs across `/issues/*`), and the stub
 * response is a whole ScheduleResponse rather than the three fields the
 * component happens to read.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LaterPopover } from './LaterPopover';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));
vi.mock('../../services/parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

const ISSUE_ID = 347474243723822;

const scheduleResponse = {
  id: '4021d6f2-0d4c-4a3a-9f52-9e0c2d5b7a11',
  user_id: 'e2a1c0f4-1111-2222-3333-444455556666',
  name: 'Wake issue 347474243723822',
  cron_expr: null,
  task_type: 'issue_wakeup',
  payload: { issue_id: ISSUE_ID, text: 'check the render', once: true },
  lane: 'scheduled',
  enabled: true,
  last_fired_at: null,
  next_fire_at: '2026-09-10T10:15:00+00:00',
  fire_count: 0,
  fail_count: 0,
  last_error: null,
  created_at: '2026-09-10T09:15:00+00:00',
  updated_at: '2026-09-10T09:15:00+00:00',
  timezone: 'Asia/Shanghai',
  consecutive_fails: 0,
  paused_at: null,
  pause_reason: null,
  skipped_count: 0,
  stale_after_minutes: 60,
};

// The production error envelope (app/core/exceptions.py), not FastAPI's bare
// {detail} — a component that only reads `detail` looks fine here and shows
// nothing useful in production.
const errorBody = {
  success: false,
  error: 'fire_at must be in the future',
  code: 'http_400',
  request_id: 'req-1',
  details: { code: 'fire_at_in_past' },
};

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify(scheduleResponse), { status: 201, headers: { 'Content-Type': 'application/json' } }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => vi.restoreAllMocks());

describe('LaterPopover', () => {
  it('posts a one-shot wake-up with a NUMBER issue id', async () => {
    const onScheduled = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={onScheduled} />);
    fireEvent.click(screen.getByTestId('later-preset-in1h'));
    fireEvent.click(screen.getByTestId('later-confirm'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.test/api/v1/schedules');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.task_type).toBe('issue_wakeup');
    expect(typeof body.fire_at).toBe('string');
    expect(body.payload).toEqual({ issue_id: ISSUE_ID, text: 'check the render', once: true });
    expect(typeof body.payload.issue_id).toBe('number');
    await waitFor(() => expect(onScheduled).toHaveBeenCalled());
  });

  it('shows the rejection and does not claim success', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(errorBody), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    const onScheduled = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={onScheduled} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    expect(await screen.findByTestId('later-error')).toBeInTheDocument();
    expect(onScheduled).not.toHaveBeenCalled();
  });

  it('will not schedule an empty message', () => {
    render(<LaterPopover issueId={ISSUE_ID} text="" onClose={vi.fn()} onScheduled={vi.fn()} />);
    expect(screen.getByTestId('later-confirm')).toBeDisabled();
    expect(screen.getByTestId('later-need-text')).toBeInTheDocument();
  });

  it('says what happens when it fires', () => {
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={vi.fn()} onScheduled={vi.fn()} />);
    expect(screen.getByTestId('later-popover')).toHaveAttribute('role', 'dialog');
    expect(screen.getByTestId('later-explain').textContent).toContain('Fires once');
  });

  it('a custom time replaces the chosen preset', async () => {
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-custom-toggle'));
    fireEvent.change(screen.getByTestId('later-custom'), { target: { value: '2026-12-01T08:30' } });
    fireEvent.click(screen.getByTestId('later-confirm'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string);
    expect(new Date(body.fire_at).getTime()).toBe(new Date('2026-12-01T08:30').getTime());
  });
});
