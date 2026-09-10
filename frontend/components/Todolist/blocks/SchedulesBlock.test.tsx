/**
 * harness 2b-2 §5-2 — the right-rail "Schedules" block.
 *
 * Rows come from `GET /issues/{id}/schedules`, whose items are the projection
 * the endpoint actually returns (a one-shot has `cron_expr: null` and a
 * `fire_at`; a routine is the other way round). A failed cancel must leave
 * the row where it is — a row that vanishes while the wake-up is still armed
 * is the silent no-op this project keeps banning.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IssueScheduleItem } from '../../../services/schedulesService';
import type { IssueBlockContext } from '../issueBlocks';
import { SchedulesBlockView, schedulesBlock } from './SchedulesBlock';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

const listIssueSchedules = vi.fn();
const remove = vi.fn();
vi.mock('../../../services/schedulesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../services/schedulesService')>();
  return {
    ...mod,
    listIssueSchedules: (...a: unknown[]) => listIssueSchedules(...a),
    schedulesService: { ...mod.schedulesService, remove: (...a: unknown[]) => remove(...a) },
  };
});
vi.mock('../LaterPopover', () => ({ LaterPopover: () => <div data-testid="later-popover" /> }));

const ISSUE_ID = 347474243723822;

const once: IssueScheduleItem = {
  id: 'sc-1', task_type: 'issue_wakeup', fire_at: '2026-09-11T01:00:00+00:00',
  cron_expr: null, text: 'check the render', created_by: 'user', enabled: true, pause_reason: null,
};
const routine: IssueScheduleItem = {
  id: 'sc-2', task_type: 'agent_routine', fire_at: '2026-09-11T02:00:00+00:00',
  cron_expr: '0 9 * * 1', text: 'Weekly sweep', created_by: 'agent', enabled: true, pause_reason: null,
};

function ctx(): IssueBlockContext {
  return {
    issue: { id: ISSUE_ID },
    rollup: { issue_id: String(ISSUE_ID) } as never,
    originKind: null,
    phase: 'running',
    env: { schedulesRefreshKey: 0 },
  };
}

beforeEach(() => {
  listIssueSchedules.mockReset().mockResolvedValue([once, routine]);
  remove.mockReset().mockResolvedValue(undefined);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SchedulesBlockView', () => {
  it('draws one row per schedule, telling one-shot from routine', async () => {
    render(<SchedulesBlockView ctx={ctx()} />);
    await waitFor(() => expect(screen.getAllByTestId('schedule-row')).toHaveLength(2));
    const rows = screen.getAllByTestId('schedule-row');
    expect(rows[0].textContent).toContain('check the render');
    expect(rows[0].textContent).toContain('Once');
    expect(rows[1].textContent).toContain('0 9 * * 1');
    expect(rows[1].textContent).toContain('Routine');
  });

  it('renders nothing at all when there is nothing timed', async () => {
    listIssueSchedules.mockResolvedValue([]);
    const { container } = render(<SchedulesBlockView ctx={ctx()} />);
    await waitFor(() => expect(listIssueSchedules).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('cancelling deletes that schedule and drops the row', async () => {
    render(<SchedulesBlockView ctx={ctx()} />);
    await waitFor(() => expect(screen.getAllByTestId('schedule-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('schedule-cancel')[0]);
    await waitFor(() => expect(remove).toHaveBeenCalledWith('sc-1'));
    await waitFor(() => expect(screen.getAllByTestId('schedule-row')).toHaveLength(1));
  });

  it('a failed cancel keeps the row and says so', async () => {
    remove.mockRejectedValue(new Error('Schedules API 500'));
    render(<SchedulesBlockView ctx={ctx()} />);
    await waitFor(() => expect(screen.getAllByTestId('schedule-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('schedule-cancel')[0]);
    expect(await screen.findByTestId('schedules-error')).toBeInTheDocument();
    expect(screen.getAllByTestId('schedule-row')).toHaveLength(2);
  });

  it('a failed read says the panel is broken rather than showing an empty list', async () => {
    listIssueSchedules.mockRejectedValue(new Error('Issue schedules API 500'));
    render(<SchedulesBlockView ctx={ctx()} />);
    expect(await screen.findByTestId('schedules-error')).toBeInTheDocument();
  });

  it('sits in the context rail between Budget and Links', () => {
    expect(schedulesBlock.zone).toBe('context');
    expect(schedulesBlock.order).toBeGreaterThan(50);
    expect(schedulesBlock.order).toBeLessThan(60);
    expect(schedulesBlock.match(ctx())).toBe(true);
    expect(schedulesBlock.match({ ...ctx(), rollup: null })).toBe(false);
  });
});
