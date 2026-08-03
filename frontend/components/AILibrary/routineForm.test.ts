import { describe, it, expect } from 'vitest';
import {
  FREQUENCY_PRESETS,
  buildCron,
  buildRoutinePayload,
  emptyRoutineForm,
  validateRoutineForm,
  type RoutineFormValues,
} from './routineForm';

const form = (over: Partial<RoutineFormValues> = {}): RoutineFormValues => ({
  ...emptyRoutineForm(),
  name: 'Morning digest',
  promptMd: 'Summarize yesterday.',
  ...over,
});

describe('buildCron', () => {
  it('builds a daily expression at the chosen time', () => {
    expect(buildCron('daily', '21', '00')).toBe('0 21 * * *');
  });

  it('builds a weekday-only expression', () => {
    expect(buildCron('weekdays', '09', '30')).toBe('30 9 * * 1-5');
  });

  it('builds a weekly (Monday) expression', () => {
    expect(buildCron('weekly', '09', '00')).toBe('0 9 * * 1');
  });

  it('keeps the minute for hourly and ignores the hour', () => {
    // Hourly at :15 must be `15 * * * *`, not `15 9 * * *`.
    expect(buildCron('hourly', '09', '15')).toBe('15 * * * *');
  });

  it('strips leading zeros so cron gets plain integers', () => {
    expect(buildCron('daily', '07', '05')).toBe('5 7 * * *');
  });

  it('returns null for custom, where the user writes the expression', () => {
    expect(buildCron('custom', '09', '00')).toBeNull();
  });
});

describe('FREQUENCY_PRESETS', () => {
  it('drops the every-15-minutes option', () => {
    // YAGNI (spec): a 15-minute agent routine burns budget on an interval
    // nobody asked for, and every real routine here is daily or slower.
    const keys = FREQUENCY_PRESETS.map((p) => p.key);
    expect(keys).toEqual(['hourly', 'daily', 'weekdays', 'weekly', 'custom']);
  });
});

describe('buildRoutinePayload', () => {
  it('carries the two fields the backend rejects the request without', () => {
    const payload = buildRoutinePayload(form(), 'script_ai');
    expect(payload.task_type).toBe('agent_routine');
    expect(payload.payload?.agent_slug).toBe('script_ai');
    expect(payload.payload?.prompt_md).toBe('Summarize yesterday.');
  });

  it('uses the built cron for a preset frequency', () => {
    const payload = buildRoutinePayload(
      form({ frequency: 'daily', hour: '21', minute: '00' }),
      'script_ai',
    );
    expect(payload.cron_expr).toBe('0 21 * * *');
  });

  it('uses the raw expression for a custom frequency', () => {
    const payload = buildRoutinePayload(
      form({ frequency: 'custom', customCron: '*/30 8-18 * * *' }),
      'script_ai',
    );
    expect(payload.cron_expr).toBe('*/30 8-18 * * *');
  });

  it('passes the delivery policy through', () => {
    const payload = buildRoutinePayload(
      form({ deliveryPolicy: 'always' }),
      'script_ai',
    );
    expect(payload.payload?.delivery_policy).toBe('always');
  });

  it('trims the name', () => {
    expect(buildRoutinePayload(form({ name: '  Digest ' }), 'a').name).toBe('Digest');
  });
});

describe('validateRoutineForm', () => {
  it('accepts a complete form', () => {
    expect(validateRoutineForm(form())).toEqual([]);
  });

  it('requires a name and a task description', () => {
    expect(validateRoutineForm(form({ name: '  ' }))).toContain('name');
    expect(validateRoutineForm(form({ promptMd: '' }))).toContain('promptMd');
  });

  it('requires an expression when the frequency is custom', () => {
    expect(
      validateRoutineForm(form({ frequency: 'custom', customCron: '' })),
    ).toContain('customCron');
    expect(
      validateRoutineForm(form({ frequency: 'custom', customCron: '0 9 * * *' })),
    ).toEqual([]);
  });
});
