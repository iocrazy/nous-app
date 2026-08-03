// frontend/components/AILibrary/routineForm.ts
// Form model for agent routines (B6, spec 2026-08-02 §B6).
//
// A routine is a user_schedules row with task_type='agent_routine': each
// cron fire creates an issue assigned to this agent, and the agent's answer
// comes back as a reply on that issue.
//
// Pure so the cron construction is testable — an off-by-one in the field
// order silently schedules something for the wrong time, which nobody
// notices until the run doesn't happen.

import type { ScheduleCreatePayload } from '../../services/schedulesService';

export type RoutineFrequency =
  | 'hourly'
  | 'daily'
  | 'weekdays'
  | 'weekly'
  | 'custom';

export const FREQUENCY_PRESETS: {
  key: RoutineFrequency;
  labelKey: string;
  label: string;
}[] = [
  { key: 'hourly', labelKey: 'aiLibrary.agents.routines.freqHourly', label: 'Hourly' },
  { key: 'daily', labelKey: 'aiLibrary.agents.routines.freqDaily', label: 'Daily' },
  {
    key: 'weekdays',
    labelKey: 'aiLibrary.agents.routines.freqWeekdays',
    label: 'Weekdays',
  },
  { key: 'weekly', labelKey: 'aiLibrary.agents.routines.freqWeekly', label: 'Weekly' },
  { key: 'custom', labelKey: 'aiLibrary.agents.routines.freqCustom', label: 'Custom' },
];

export type RoutineDeliveryPolicy = 'skip_if_active' | 'always';

export interface RoutineFormValues {
  name: string;
  promptMd: string;
  frequency: RoutineFrequency;
  /** 24h hour, zero-padded from an <input type="time">-ish picker. */
  hour: string;
  minute: string;
  /** Raw cron, used only when frequency is 'custom'. */
  customCron: string;
  timezone: string;
  deliveryPolicy: RoutineDeliveryPolicy;
}

const BROWSER_TZ: string = ((): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
})();

export function emptyRoutineForm(): RoutineFormValues {
  return {
    name: '',
    promptMd: '',
    frequency: 'daily',
    hour: '09',
    minute: '00',
    customCron: '',
    timezone: BROWSER_TZ,
    deliveryPolicy: 'skip_if_active',
  };
}

/** Zero-padded picker values → the plain integers cron expects. */
const num = (v: string): number => Number.parseInt(v, 10) || 0;

/** Build the 5-field cron for a preset frequency; null for 'custom'. */
export function buildCron(
  frequency: RoutineFrequency,
  hour: string,
  minute: string,
): string | null {
  const h = num(hour);
  const m = num(minute);
  switch (frequency) {
    // Hourly keeps the minute and drops the hour — an hourly routine at :15
    // runs every hour at quarter past, not once a day.
    case 'hourly':
      return `${m} * * * *`;
    case 'daily':
      return `${m} ${h} * * *`;
    case 'weekdays':
      return `${m} ${h} * * 1-5`;
    case 'weekly':
      return `${m} ${h} * * 1`;
    default:
      return null;
  }
}

export type RoutineFormError = 'name' | 'promptMd' | 'customCron';

export function validateRoutineForm(form: RoutineFormValues): RoutineFormError[] {
  const errors: RoutineFormError[] = [];
  if (form.name.trim().length === 0) errors.push('name');
  // Both of these are what the backend 400s on; catching them here keeps the
  // failure legible instead of a raw API error in a toast.
  if (form.promptMd.trim().length === 0) errors.push('promptMd');
  if (form.frequency === 'custom' && form.customCron.trim().length === 0) {
    errors.push('customCron');
  }
  return errors;
}

export function buildRoutinePayload(
  form: RoutineFormValues,
  agentSlug: string,
): ScheduleCreatePayload {
  return {
    name: form.name.trim(),
    cron_expr:
      buildCron(form.frequency, form.hour, form.minute) ?? form.customCron.trim(),
    task_type: 'agent_routine',
    timezone: form.timezone,
    payload: {
      agent_slug: agentSlug,
      prompt_md: form.promptMd.trim(),
      delivery_policy: form.deliveryPolicy,
    },
  };
}
