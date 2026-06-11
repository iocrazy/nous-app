import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Play, Plus, Trash2 } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import {
  schedulesService,
  type ScheduleResponse,
} from '../../services/schedulesService';
import { useToast } from '../Toast';

// Agent Routines tab (paperclip R1). A routine is a user_schedules row with
// task_type='agent_routine': on each cron fire the master scheduler creates
// an issue (origin_kind='routine') assigned to this agent and dispatches the
// existing execute_issue chain — results land as issue replies.

const CRON_PRESETS: Array<{ label: string; expr: string }> = [
  { label: 'Daily 9:00', expr: '0 9 * * *' },
  { label: 'Hourly', expr: '0 * * * *' },
  { label: 'Weekly Mon 9:00', expr: '0 9 * * 1' },
  { label: 'Every 15 min', expr: '*/15 * * * *' },
];

interface RoutineFormState {
  name: string;
  cron_expr: string;
  prompt_md: string;
  delivery_policy: 'skip_if_active' | 'always';
}

const EMPTY_FORM: RoutineFormState = {
  name: '',
  cron_expr: '0 9 * * *',
  prompt_md: '',
  delivery_policy: 'skip_if_active',
};

const RoutineForm: React.FC<{
  initial: RoutineFormState;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (form: RoutineFormState) => void;
}> = ({ initial, submitting, onCancel, onSubmit }) => {
  const { t } = useTranslation();
  const [form, setForm] = useState<RoutineFormState>(initial);
  const isPreset = CRON_PRESETS.some((p) => p.expr === form.cron_expr);

  return (
    <div className="space-y-3 rounded-lg border border-zinc-700 bg-zinc-900/60 p-4">
      <label className="block text-xs">
        <span className="font-medium text-zinc-400">
          {t('aiLibrary.agents.routines.nameLabel', 'Name')}
        </span>
        <input
          type="text"
          value={form.name}
          maxLength={200}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          placeholder={t('aiLibrary.agents.routines.namePlaceholder', 'Daily digest')}
          className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
        />
      </label>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-xs">
          <span className="font-medium text-zinc-400">
            {t('aiLibrary.agents.routines.scheduleLabel', 'Schedule')}
          </span>
          <select
            value={isPreset ? form.cron_expr : '__custom__'}
            onChange={(e) => {
              const v = e.target.value;
              if (v !== '__custom__') setForm((f) => ({ ...f, cron_expr: v }));
            }}
            className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
          >
            {CRON_PRESETS.map((p) => (
              <option key={p.expr} value={p.expr}>{p.label}</option>
            ))}
            <option value="__custom__">
              {t('aiLibrary.agents.routines.customCron', 'Custom cron…')}
            </option>
          </select>
          <input
            type="text"
            value={form.cron_expr}
            onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
            className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 font-mono text-xs text-zinc-300 focus:border-indigo-500 focus:outline-none"
          />
        </label>

        <label className="block text-xs">
          <span className="font-medium text-zinc-400">
            {t('aiLibrary.agents.routines.policyLabel', 'If previous run still open')}
          </span>
          <select
            value={form.delivery_policy}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                delivery_policy: e.target.value as RoutineFormState['delivery_policy'],
              }))
            }
            className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
          >
            <option value="skip_if_active">
              {t('aiLibrary.agents.routines.policySkip', 'Skip this fire (recommended)')}
            </option>
            <option value="always">
              {t('aiLibrary.agents.routines.policyAlways', 'Fire anyway')}
            </option>
          </select>
        </label>
      </div>

      <label className="block text-xs">
        <span className="font-medium text-zinc-400">
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
          className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
        />
      </label>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700"
        >
          {t('common.cancel', 'Cancel')}
        </button>
        <button
          type="button"
          disabled={submitting || !form.name.trim() || !form.prompt_md.trim()}
          onClick={() => onSubmit(form)}
          className="rounded-md border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50"
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
          payload,
        });
      } else {
        await schedulesService.create({
          name: form.name,
          cron_expr: form.cron_expr,
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
        <p className="text-xs text-zinc-500">
          {t(
            'aiLibrary.agents.routines.intro',
            'Each fire creates an issue assigned to this agent and runs it automatically — results land as issue replies.',
          )}
        </p>
        {!formOpen && (
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20"
          >
            <Plus size={12} />
            {t('aiLibrary.agents.routines.add', 'New routine')}
          </button>
        )}
      </header>

      {formOpen && (
        <RoutineForm
          initial={
            editing
              ? {
                  name: editing.name,
                  cron_expr: editing.cron_expr,
                  prompt_md: String(
                    (editing.payload as { prompt_md?: string }).prompt_md ?? '',
                  ),
                  delivery_policy:
                    ((editing.payload as { delivery_policy?: string })
                      .delivery_policy as RoutineFormState['delivery_policy']) ??
                    'skip_if_active',
                }
              : EMPTY_FORM
          }
          submitting={submitting}
          onCancel={() => {
            setFormOpen(false);
            setEditing(null);
          }}
          onSubmit={(f) => void submit(f)}
        />
      )}

      {routines === null ? (
        <p className="text-sm text-zinc-500">{t('common.loading', 'Loading…')}</p>
      ) : routines.length === 0 && !formOpen ? (
        <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 px-3 py-8 text-center text-sm text-zinc-500">
          {t(
            'aiLibrary.agents.routines.empty',
            'No routines yet. Create one to have this agent work on a schedule.',
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {routines.map((r) => {
            const lastIssue = (r.payload as { last_issue_id?: number }).last_issue_id;
            return (
              <div
                key={r.id}
                className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(r);
                      setFormOpen(true);
                    }}
                    className="truncate text-left text-sm font-medium text-zinc-100 hover:text-indigo-300"
                  >
                    {r.name}
                  </button>
                  <span className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800 px-1.5 py-px font-mono text-[10px] text-zinc-400">
                    <Clock size={10} />
                    {r.cron_expr}
                  </span>
                  {!r.enabled && (
                    <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-px text-[10px] text-amber-300">
                      {t('aiLibrary.agents.routines.disabled', 'disabled')}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => void fireNow(r)}
                      title={t('aiLibrary.agents.routines.fireNow', 'Run now')}
                      className="rounded-md border border-zinc-700 bg-zinc-800 p-1.5 text-zinc-300 hover:bg-zinc-700"
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
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-zinc-500">
                  <span>
                    {t('aiLibrary.agents.routines.nextFire', 'Next')}:{' '}
                    {r.next_fire_at ? new Date(r.next_fire_at).toLocaleString() : '—'}
                  </span>
                  <span>
                    {t('aiLibrary.agents.routines.lastFire', 'Last')}:{' '}
                    {r.last_fired_at ? new Date(r.last_fired_at).toLocaleString() : '—'}
                  </span>
                  {lastIssue != null && (
                    <span>
                      {t('aiLibrary.agents.routines.lastIssue', 'Last issue')}: #{lastIssue}
                    </span>
                  )}
                  {r.last_error && (
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
