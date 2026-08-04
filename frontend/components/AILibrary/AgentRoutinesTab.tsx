import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Play, Plus, Trash2 } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import {
  schedulesService,
  type ScheduleResponse,
} from '../../services/schedulesService';
import { useToast } from '../Toast';
import { UiSelect } from '../ui';

// Agent Routines tab (paperclip R1). A routine is a user_schedules row with
// task_type='agent_routine': on each cron fire the master scheduler creates
// an issue (origin_kind='routine') assigned to this agent and dispatches the
// existing execute_issue chain — results land as issue replies.

// Preset cron shortcuts for the inline editor. "Every 15 min" is gone on
// purpose (spec YAGNI): a quarter-hourly agent routine burns budget on a
// cadence nobody asked for, and every real routine here is daily or slower.
// Weekdays covers the common "work mornings" case the old set missed.
const CRON_PRESETS: Array<{ label: string; expr: string }> = [
  { label: 'Daily 09:00', expr: '0 9 * * *' },
  { label: 'Weekdays 09:00', expr: '0 9 * * 1-5' },
  { label: 'Hourly', expr: '0 * * * *' },
  { label: 'Weekly Mon 09:00', expr: '0 9 * * 1' },
];

// The browser's IANA timezone — the sensible default for a new routine.
const BROWSER_TZ: string = ((): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
})();

// A curated set of common IANA zones. The browser tz + the routine's current
// tz are merged in at render time so every valid value is always selectable.
const COMMON_TIMEZONES: string[] = [
  'UTC',
  'America/Los_Angeles',
  'America/Denver',
  'America/Chicago',
  'America/New_York',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Moscow',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Asia/Hong_Kong',
  'Australia/Sydney',
  'Pacific/Auckland',
];

function timezoneOptions(current: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const tz of [BROWSER_TZ, ...COMMON_TIMEZONES, current]) {
    if (tz && !seen.has(tz)) {
      seen.add(tz);
      out.push(tz);
    }
  }
  return out;
}

interface RoutineFormState {
  name: string;
  cron_expr: string;
  timezone: string;
  prompt_md: string;
  delivery_policy: 'skip_if_active' | 'always';
}

function emptyForm(): RoutineFormState {
  return {
    name: '',
    cron_expr: '0 9 * * *',
    timezone: BROWSER_TZ,
    prompt_md: '',
    delivery_policy: 'skip_if_active',
  };
}

const RoutineForm: React.FC<{
  initial: RoutineFormState;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (form: RoutineFormState) => void;
}> = ({ initial, submitting, onCancel, onSubmit }) => {
  const { t } = useTranslation();
  const [form, setForm] = useState<RoutineFormState>(initial);
  const isPreset = CRON_PRESETS.some((p) => p.expr === form.cron_expr);
  const tzOptions = timezoneOptions(form.timezone);

  return (
    <div className="space-y-3 rounded-lg border border-ink-700 bg-ink-900/60 p-4">
      <label className="block text-xs">
        <span className="font-medium text-ink-400">
          {t('aiLibrary.agents.routines.nameLabel', 'Name')}
        </span>
        <input
          type="text"
          value={form.name}
          maxLength={200}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          placeholder={t('aiLibrary.agents.routines.namePlaceholder', 'Daily digest')}
          className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
        />
      </label>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-xs">
          <span className="font-medium text-ink-400">
            {t('aiLibrary.agents.routines.scheduleLabel', 'Schedule')}
          </span>
          <UiSelect
            value={isPreset ? form.cron_expr : '__custom__'}
            onChange={(e) => {
              const v = e.target.value;
              if (v !== '__custom__') setForm((f) => ({ ...f, cron_expr: v }));
            }}
            className="mt-1 w-full"
          >
            {CRON_PRESETS.map((p) => (
              <option key={p.expr} value={p.expr}>{p.label}</option>
            ))}
            <option value="__custom__">
              {t('aiLibrary.agents.routines.customCron', 'Custom cron…')}
            </option>
          </UiSelect>
          <input
            type="text"
            value={form.cron_expr}
            onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-1.5 font-mono text-xs text-ink-300 focus:border-indigo-500 focus:outline-none"
          />
          <p className="mt-1 text-[10px] text-ink-500">
            {t(
              'aiLibrary.agents.routines.cronTzHint',
              'Cron hours are interpreted in the timezone below.',
            )}
          </p>
        </label>

        <label className="block text-xs">
          <span className="font-medium text-ink-400">
            {t('aiLibrary.agents.routines.policyLabel', 'If previous run still open')}
          </span>
          <UiSelect
            value={form.delivery_policy}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                delivery_policy: e.target.value as RoutineFormState['delivery_policy'],
              }))
            }
            className="mt-1 w-full"
          >
            <option value="skip_if_active">
              {t('aiLibrary.agents.routines.policySkip', 'Skip this fire (recommended)')}
            </option>
            <option value="always">
              {t('aiLibrary.agents.routines.policyAlways', 'Fire anyway')}
            </option>
          </UiSelect>
        </label>
      </div>

      <label className="block text-xs">
        <span className="font-medium text-ink-400">
          {t('aiLibrary.agents.routines.timezoneLabel', 'Timezone')}
        </span>
        <UiSelect
          value={form.timezone}
          onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
          className="mt-1 w-full"
        >
          {tzOptions.map((tz) => (
            <option key={tz} value={tz}>{tz}</option>
          ))}
        </UiSelect>
      </label>

      <label className="block text-xs">
        <span className="font-medium text-ink-400">
          {t('aiLibrary.agents.routines.promptLabel', 'Instructions (sent to the agent on each fire)')}
        </span>
        <textarea
          rows={5}
          value={form.prompt_md}
          onChange={(e) => setForm((f) => ({ ...f, prompt_md: e.target.value }))}
          placeholder={t(
            'aiLibrary.agents.routines.promptPlaceholder',
            'Summarize the resources downloaded in the last 24 hours…',
          )}
          className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
        />
      </label>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-ink-700 bg-ink-800 px-3 py-1.5 text-xs font-medium text-ink-300 hover:bg-ink-700"
        >
          {t('common.cancel', 'Cancel')}
        </button>
        <button
          type="button"
          disabled={submitting || !form.name.trim() || !form.prompt_md.trim()}
          onClick={() => onSubmit(form)}
          className="rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-medium text-[var(--accent-text)] hover:bg-[var(--accent-soft)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? t('common.saving', 'Saving…') : t('common.save', 'Save')}
        </button>
      </div>
    </div>
  );
};

export const AgentRoutinesTab: React.FC<{ agent: AILibraryAgent }> = ({ agent }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [routines, setRoutines] = useState<ScheduleResponse[] | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formInitial, setFormInitial] = useState<RoutineFormState>(emptyForm);
  // Bumped on every open so RoutineForm remounts with fresh initial state even
  // when it's already open (e.g. switching from edit to a template).
  const [formKey, setFormKey] = useState(0);
  const [editing, setEditing] = useState<ScheduleResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const all = await schedulesService.list();
      setRoutines(
        all.filter(
          (s) =>
            s.task_type === 'agent_routine'
            && (s.payload as { agent_slug?: string }).agent_slug === agent.slug,
        ),
      );
    } catch (err) {
      console.error('[AgentRoutinesTab] list failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  }, [agent.slug, addToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const openForm = useCallback(
    (initial: RoutineFormState, editRow: ScheduleResponse | null): void => {
      setEditing(editRow);
      setFormInitial(initial);
      setFormKey((k) => k + 1);
      setFormOpen(true);
    },
    [],
  );

  const editForm = (r: ScheduleResponse): RoutineFormState => ({
    name: r.name,
    cron_expr: r.cron_expr,
    timezone: r.timezone || BROWSER_TZ,
    prompt_md: String((r.payload as { prompt_md?: string }).prompt_md ?? ''),
    delivery_policy:
      ((r.payload as { delivery_policy?: string }).delivery_policy as
        RoutineFormState['delivery_policy']) ?? 'skip_if_active',
  });

  const submit = async (form: RoutineFormState): Promise<void> => {
    setSubmitting(true);
    try {
      const payload = {
        agent_slug: agent.slug,
        prompt_md: form.prompt_md,
        delivery_policy: form.delivery_policy,
        // Preserve the delivery gate across edits.
        ...(editing
          ? { last_issue_id: (editing.payload as { last_issue_id?: number }).last_issue_id }
          : {}),
      };
      if (editing) {
        await schedulesService.update(editing.id, {
          name: form.name,
          cron_expr: form.cron_expr,
          timezone: form.timezone,
          payload,
        });
      } else {
        await schedulesService.create({
          name: form.name,
          cron_expr: form.cron_expr,
          timezone: form.timezone,
          task_type: 'agent_routine',
          payload,
          enabled: true,
        });
      }
      addToast(t('aiLibrary.agents.routines.saved', 'Routine saved'), 'success');
      setFormOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      console.error('[AgentRoutinesTab] save failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const toggle = async (r: ScheduleResponse): Promise<void> => {
    try {
      await schedulesService.update(r.id, { enabled: !r.enabled });
      await load();
    } catch (err) {
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  const fireNow = async (r: ScheduleResponse): Promise<void> => {
    try {
      await schedulesService.fireNow(r.id);
      addToast(t('aiLibrary.agents.routines.fired', 'Routine fired'), 'success');
      await load();
    } catch (err) {
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  const resume = async (r: ScheduleResponse): Promise<void> => {
    try {
      await schedulesService.resume(r.id);
      addToast(t('aiLibrary.agents.routines.resumed', 'Routine resumed'), 'success');
      await load();
    } catch (err) {
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  const remove = async (r: ScheduleResponse): Promise<void> => {
    if (!window.confirm(t('aiLibrary.agents.routines.deleteConfirm', 'Delete this routine?'))) {
      return;
    }
    try {
      await schedulesService.remove(r.id);
      await load();
    } catch (err) {
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between gap-3">
        <p className="text-xs text-ink-500">
          {t(
            'aiLibrary.agents.routines.intro',
            'Each fire creates an issue assigned to this agent and runs it automatically — results land as issue replies.',
          )}
        </p>
        {/* Creation lives on the workbench card header ("New task" →
            NewRoutineModal). This tab used to carry two more entry points of
            its own — "Daily topic scout" and "New routine" — so the same card
            offered three ways to make the same thing, two of them driving a
            cron-first form and one a natural-language one. Editing an
            existing routine still opens the form below. */}
      </header>

      {formOpen && (
        <RoutineForm
          key={formKey}
          initial={formInitial}
          submitting={submitting}
          onCancel={() => {
            setFormOpen(false);
            setEditing(null);
          }}
          onSubmit={(f) => void submit(f)}
        />
      )}

      {routines === null ? (
        <p className="text-sm text-ink-500">{t('common.loading', 'Loading…')}</p>
      ) : routines.length === 0 && !formOpen ? (
        <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-3 py-8 text-center text-sm text-ink-500">
          {t(
            'aiLibrary.agents.routines.empty',
            'No routines yet. Create one to have this agent work on a schedule.',
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {routines.map((r) => {
            const lastIssue = (r.payload as { last_issue_id?: number }).last_issue_id;
            const isPaused = !!r.paused_at;
            return (
              <div
                key={r.id}
                className="rounded-lg border border-ink-800 bg-ink-900/60 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openForm(editForm(r), r)}
                    className="truncate text-left text-sm font-medium text-ink-100 hover:text-[var(--accent-text)]"
                  >
                    {r.name}
                  </button>
                  <span className="inline-flex items-center gap-1 rounded border border-ink-700 bg-ink-800 px-1.5 py-px font-mono text-[10px] text-ink-400">
                    <Clock size={10} />
                    {r.cron_expr}
                  </span>
                  <span className="rounded border border-ink-700 bg-ink-800 px-1.5 py-px text-[10px] text-ink-500">
                    {r.timezone}
                  </span>
                  {isPaused ? (
                    <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-px text-[10px] font-medium text-warn">
                      {t('aiLibrary.agents.routines.paused', 'Paused')}
                    </span>
                  ) : (
                    !r.enabled && (
                      <span className="rounded border border-ink-700 bg-ink-800 px-1.5 py-px text-[10px] text-ink-400">
                        {t('aiLibrary.agents.routines.disabled', 'disabled')}
                      </span>
                    )
                  )}
                  <span className="ml-auto flex items-center gap-1.5">
                    {isPaused && (
                      <button
                        type="button"
                        onClick={() => void resume(r)}
                        className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-warn hover:bg-amber-500/20"
                      >
                        {t('aiLibrary.agents.routines.resume', 'Resume')}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void fireNow(r)}
                      title={t('aiLibrary.agents.routines.fireNow', 'Run now')}
                      className="rounded-md border border-ink-700 bg-ink-800 p-1.5 text-ink-300 hover:bg-ink-700"
                    >
                      <Play size={12} />
                    </button>
                    <label className="inline-flex cursor-pointer items-center">
                      <input
                        type="checkbox"
                        checked={r.enabled}
                        onChange={() => void toggle(r)}
                        className="h-3.5 w-3.5"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => void remove(r)}
                      title={t('common.delete', 'Delete')}
                      className="rounded-md border border-red-500/30 bg-red-500/10 p-1.5 text-red-300 hover:bg-red-500/20"
                    >
                      <Trash2 size={12} />
                    </button>
                  </span>
                </div>
                {isPaused && r.pause_reason && (
                  <p className="mt-1.5 text-[11px] text-amber-400/90">{r.pause_reason}</p>
                )}
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-ink-500">
                  <span>
                    {t('aiLibrary.agents.routines.nextFire', 'Next')}:{' '}
                    {r.next_fire_at ? new Date(r.next_fire_at).toLocaleString() : '—'}
                  </span>
                  <span>
                    {t('aiLibrary.agents.routines.lastFire', 'Last')}:{' '}
                    {r.last_fired_at ? new Date(r.last_fired_at).toLocaleString() : '—'}
                  </span>
                  {r.skipped_count > 0 && (
                    <span>
                      {t('aiLibrary.agents.routines.skipped', 'Skipped')}: {r.skipped_count}
                    </span>
                  )}
                  {lastIssue != null && (
                    <span>
                      {t('aiLibrary.agents.routines.lastIssue', 'Last issue')}: #{lastIssue}
                    </span>
                  )}
                  {!isPaused && r.last_error && (
                    <span className="text-red-400">{r.last_error}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
};

export default AgentRoutinesTab;
