// frontend/services/schedulesService.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { schedulesService, type ScheduleResponse } from './schedulesService';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer t' }),
}));

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

// A full W2a-era schedule row — carries the autopilot columns end to end.
const FULL_ROW: ScheduleResponse = {
  id: '9007199254740993', // snowflake-shaped; stays a string
  user_id: 'u1',
  name: 'Daily scout',
  cron_expr: '0 9 * * *',
  task_type: 'agent_routine',
  payload: { agent_slug: 'x', prompt_md: 'hi' },
  lane: 'scheduled',
  enabled: true,
  last_fired_at: null,
  next_fire_at: '2026-07-20T13:00:00+00:00',
  fire_count: 3,
  fail_count: 5,
  last_error: null,
  created_at: '2026-07-01T00:00:00+00:00',
  updated_at: '2026-07-01T00:00:00+00:00',
  timezone: 'America/New_York',
  consecutive_fails: 0,
  paused_at: null,
  pause_reason: null,
  skipped_count: 2,
  stale_after_minutes: 60,
};

describe('schedulesService', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('create forwards timezone and parses the autopilot fields', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(FULL_ROW), { status: 200 }));

    const out = await schedulesService.create({
      name: 'Daily scout',
      cron_expr: '0 9 * * *',
      task_type: 'agent_routine',
      timezone: 'America/New_York',
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/schedules');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string).timezone).toBe(
      'America/New_York',
    );
    // Snowflake id survives as a string (never Number()-coerced).
    expect(out.id).toBe('9007199254740993');
    expect(out.timezone).toBe('America/New_York');
    expect(out.skipped_count).toBe(2);
    expect(out.paused_at).toBeNull();
  });

  it('update forwards timezone', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(FULL_ROW), { status: 200 }));

    await schedulesService.update('9007199254740993', { timezone: 'UTC' });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/schedules/9007199254740993');
    expect((init as RequestInit).method).toBe('PATCH');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ timezone: 'UTC' });
  });

  it('resume POSTs to /{id}/resume and returns the cleared row', async () => {
    const resumed: ScheduleResponse = {
      ...FULL_ROW,
      enabled: true,
      consecutive_fails: 0,
      paused_at: null,
      pause_reason: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(resumed), { status: 200 }));

    const out = await schedulesService.resume('9007199254740993');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/schedules/9007199254740993/resume');
    expect((init as RequestInit).method).toBe('POST');
    expect(out.enabled).toBe(true);
    expect(out.paused_at).toBeNull();
  });
});
