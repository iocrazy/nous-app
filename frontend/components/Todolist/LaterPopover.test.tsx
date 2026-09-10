/**
 * harness 2b-2 §5-2 — the "⏰ Later" popover.
 *
 * The POST body is asserted against the REAL wire shape: `issue_id` is a JSON
 * NUMBER (issue ids are unconverted BIGINTs across `/issues/*`), and the stub
 * response is a whole ScheduleResponse rather than the three fields the
 * component happens to read.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
// {detail}. `_bad_request` in schedules_router.py always puts a typed
// {code,message} under `details`; the outer `code` is only ever `http_400`.
const rejection = (code: string, message: string) => ({
  success: false,
  error: message,
  code: 'http_400',
  request_id: 'req-8f2c1a',
  details: { code, message },
});

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify(scheduleResponse), { status: 201, headers: { 'Content-Type': 'application/json' } }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// I-3: the same close conventions as DispatchConfirmDialog / ForkRunDialog.
describe('LaterPopover — closing and focus', () => {
  it('Escape closes it', () => {
    const onClose = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={onClose} onScheduled={vi.fn()} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('clicking outside closes it, clicking inside does not', () => {
    const onClose = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={onClose} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-popover'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('later-backdrop'));
    expect(onClose).toHaveBeenCalled();
  });

  it('nothing closes it while the request is in flight', async () => {
    let release: (r: Response) => void = () => undefined;
    fetchMock.mockReturnValue(new Promise<Response>((r) => { release = r; }));
    const onClose = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={onClose} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('later-backdrop'));
    expect(onClose).not.toHaveBeenCalled();
    release(new Response(JSON.stringify(scheduleResponse), { status: 201, headers: { 'Content-Type': 'application/json' } }));
  });

  it('takes focus on open and hands it back to the opener on close', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);
    const { unmount } = render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={vi.fn()} onScheduled={vi.fn()} />);
    expect(screen.getByTestId('later-popover').contains(document.activeElement)).toBe(true);
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});

// I-4: the popover owns the note, so the right-rail entry point works too.
describe('LaterPopover — the note', () => {
  it('prefills from the composer draft and sends what is in the box', async () => {
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={vi.fn()} />);
    const note = screen.getByTestId('later-note') as HTMLTextAreaElement;
    expect(note.value).toBe('check the render');
    fireEvent.change(note, { target: { value: 'check the render again' } });
    fireEvent.click(screen.getByTestId('later-confirm'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string);
    expect(body.payload.text).toBe('check the render again');
  });

  it('opened with no draft, typing a note is enough to schedule', async () => {
    render(<LaterPopover issueId={ISSUE_ID} text="" onClose={vi.fn()} onScheduled={vi.fn()} />);
    expect(screen.getByTestId('later-confirm')).toBeDisabled();
    fireEvent.change(screen.getByTestId('later-note'), { target: { value: 'sweep the inbox' } });
    expect(screen.getByTestId('later-confirm')).toBeEnabled();
    fireEvent.click(screen.getByTestId('later-confirm'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse((fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string);
    expect(body.payload.text).toBe('sweep the inbox');
  });

  it('whitespace is not a message', () => {
    render(<LaterPopover issueId={ISSUE_ID} text="" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.change(screen.getByTestId('later-note'), { target: { value: '   ' } });
    expect(screen.getByTestId('later-confirm')).toBeDisabled();
  });
});

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
    fetchMock.mockResolvedValue(new Response(JSON.stringify(rejection('fire_at_out_of_range', 'fire_at must be in the future and within 30 days')), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    const onScheduled = vi.fn();
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={onScheduled} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    expect(await screen.findByTestId('later-error')).toBeInTheDocument();
    expect(onScheduled).not.toHaveBeenCalled();
  });

  // I-2: the codes are the ones schedules_router.py actually raises.
  it.each([
    ['fire_at_out_of_range', 'within 30 days'],
    ['fire_at_timezone_required', 'time zone'],
    ['fire_at_required', 'Pick a time'],
    ['text_required', 'message'],
    ['issue_id_required', 'could not be identified'],
  ])('turns a %s rejection into a sentence a person can act on', async (code, fragment) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(rejection(code, 'server prose')), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    const err = await screen.findByTestId('later-error');
    expect(err.textContent).toContain(fragment);
  });

  it('never shows the raw envelope — no request id, no JSON, no braces', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(rejection('fire_at_out_of_range', 'fire_at must be in the future')), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    const err = await screen.findByTestId('later-error');
    expect(err.textContent).not.toContain('req-8f2c1a');
    expect(err.textContent).not.toContain('{');
    expect(err.textContent).not.toContain('success');
  });

  it('an unknown code still names itself so a bug report can carry it', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(rejection('lane_forbidden', 'nope')), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={vi.fn()} />);
    const err = await (fireEvent.click(screen.getByTestId('later-confirm')), screen.findByTestId('later-error'));
    expect(err.textContent).toContain('lane_forbidden');
  });

  it('a 500 with no typed body still says something human', async () => {
    fetchMock.mockResolvedValue(new Response('<html>502 Bad Gateway</html>', { status: 502 }));
    render(<LaterPopover issueId={ISSUE_ID} text="check the render" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-confirm'));
    const err = await screen.findByTestId('later-error');
    expect(err.textContent).toContain('Could not schedule');
    expect(err.textContent).not.toContain('<html>');
  });

  it('will not schedule an empty message', () => {
    render(<LaterPopover issueId={ISSUE_ID} text="" onClose={vi.fn()} onScheduled={vi.fn()} />);
    expect(screen.getByTestId('later-confirm')).toBeDisabled();
    expect(screen.getByTestId('later-need-text')).toBeInTheDocument();
  });

  it('the custom picker cannot reach past the backend horizon', () => {
    render(<LaterPopover issueId={ISSUE_ID} text="x" onClose={vi.fn()} onScheduled={vi.fn()} />);
    fireEvent.click(screen.getByTestId('later-custom-toggle'));
    const input = screen.getByTestId('later-custom') as HTMLInputElement;
    expect(input.min).not.toBe('');
    // 30 days is MAX_WAKEUP_HORIZON; past it the POST is a 400.
    const max = new Date(input.max).getTime();
    expect(max).toBeGreaterThan(Date.now() + 29 * 864e5);
    expect(max).toBeLessThan(Date.now() + 31 * 864e5);
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
