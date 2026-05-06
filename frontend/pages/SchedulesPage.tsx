/**
 * SchedulesPage — cron schedule CRUD UI (A 路线 PR #160).
 *
 * Lists user-defined schedules, lets users create/edit/delete cron entries
 * the master scheduler workflow scans every minute.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Clock, Plus, Pencil, Trash2, Loader2, Play, Power, PowerOff, Calendar,
} from 'lucide-react';
import {
  schedulesService,
  type ScheduleResponse,
  type ScheduleCreatePayload,
  type ScheduleUpdatePayload,
} from '../services/schedulesService';
import { useToast } from '../components/Toast';

// Backend schedules_router whitelists task_type to specific values that map
// to existing dispatch lanes — pick a sensible default that won't 400.
const EMPTY_FORM: ScheduleCreatePayload = {
  name: '',
  cron_expr: '0 9 * * *',
  task_type: 'ai_summary',
  payload: {},
  enabled: true,
};

interface ScheduleFormProps {
  initial: ScheduleCreatePayload | (ScheduleResponse & { _editing: true });
  onCancel: () => void;
  onSubmit: (payload: ScheduleCreatePayload | ScheduleUpdatePayload) => Promise<void>;
}

const ScheduleForm: React.FC<ScheduleFormProps> = ({ initial, onCancel, onSubmit }) => {
  const editing = '_editing' in initial && initial._editing;
  const [form, setForm] = useState({
    name: initial.name,
    cron_expr: initial.cron_expr,
    task_type: 'task_type' in initial ? initial.task_type : 'workflow',
    payload_json: JSON.stringify(initial.payload ?? {}, null, 2),
    enabled: initial.enabled ?? true,
  });
  const [submitting, setSubmitting] = useState(false);
  const { addToast } = useToast();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    let payloadParsed: Record<string, unknown> = {};
    try {
      payloadParsed = form.payload_json.trim() ? JSON.parse(form.payload_json) : {};
    } catch {
      addToast('Payload is not valid JSON', 'error');
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await onSubmit({
          name: form.name,
          cron_expr: form.cron_expr,
          payload: payloadParsed,
          enabled: form.enabled,
        });
      } else {
        await onSubmit({
          name: form.name,
          cron_expr: form.cron_expr,
          task_type: form.task_type,
          payload: payloadParsed,
          enabled: form.enabled,
        });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3 p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
      <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">
        {editing ? 'Edit schedule' : 'New schedule'}
      </h3>
      <label className="block text-xs">
        <span className="text-gray-700 dark:text-gray-300">Name</span>
        <input
          type="text" required maxLength={200}
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          className="mt-1 w-full px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900"
        />
      </label>
      <label className="block text-xs">
        <span className="text-gray-700 dark:text-gray-300">Cron expression</span>
        <input
          type="text" required
          value={form.cron_expr}
          onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
          placeholder="0 9 * * *"
          className="mt-1 w-full px-2 py-1 font-mono text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900"
        />
        <span className="text-[10px] text-gray-500 mt-0.5 block">5-field cron: m h dom mon dow. Example: <code>0 9 * * 1-5</code> = 9am every weekday</span>
      </label>
      {!editing && (
        <label className="block text-xs">
          <span className="text-gray-700 dark:text-gray-300">Task type</span>
          <input
            type="text" required
            value={form.task_type}
            onChange={(e) => setForm((f) => ({ ...f, task_type: e.target.value }))}
            className="mt-1 w-full px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900"
          />
        </label>
      )}
      <label className="block text-xs">
        <span className="text-gray-700 dark:text-gray-300">Payload (JSON)</span>
        <textarea
          rows={4}
          value={form.payload_json}
          onChange={(e) => setForm((f) => ({ ...f, payload_json: e.target.value }))}
          className="mt-1 w-full px-2 py-1 font-mono text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900"
        />
      </label>
      <label className="inline-flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
        />
        <span className="text-gray-700 dark:text-gray-300">Enabled</span>
      </label>
      <div className="flex justify-end gap-2 pt-2">
        <button type="button" onClick={onCancel} className="px-3 py-1 text-xs rounded border border-gray-300 dark:border-gray-600">
          Cancel
        </button>
        <button type="submit" disabled={submitting} className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
          {submitting ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  );
};

export const SchedulesPage: React.FC = () => {
  const [schedules, setSchedules] = useState<ScheduleResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ScheduleResponse | null>(null);
  const [creating, setCreating] = useState(false);
  const { addToast } = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await schedulesService.list();
      setSchedules(list);
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Failed to load schedules', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { void refresh(); }, [refresh]);

  const onCreate = async (payload: ScheduleCreatePayload | ScheduleUpdatePayload) => {
    try {
      await schedulesService.create(payload as ScheduleCreatePayload);
      addToast('Schedule created', 'success');
      setCreating(false);
      void refresh();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Create failed', 'error');
    }
  };

  const onUpdate = async (payload: ScheduleCreatePayload | ScheduleUpdatePayload) => {
    if (!editing) return;
    try {
      await schedulesService.update(editing.id, payload as ScheduleUpdatePayload);
      addToast('Schedule updated', 'success');
      setEditing(null);
      void refresh();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Update failed', 'error');
    }
  };

  const onDelete = async (id: string) => {
    if (!window.confirm('Delete this schedule?')) return;
    try {
      await schedulesService.remove(id);
      addToast('Schedule deleted', 'success');
      void refresh();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Delete failed', 'error');
    }
  };

  const onToggle = async (s: ScheduleResponse) => {
    try {
      await schedulesService.update(s.id, { enabled: !s.enabled });
      void refresh();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Toggle failed', 'error');
    }
  };

  const onFireNow = async (id: string) => {
    try {
      await schedulesService.fireNow(id);
      addToast('Manual fire triggered', 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Fire-now failed', 'error');
    }
  };

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <Calendar className="w-5 h-5" />
          Schedules
        </h1>
        {!creating && !editing && (
          <button
            onClick={() => setCreating(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" /> New
          </button>
        )}
      </header>

      {creating && (
        <ScheduleForm initial={EMPTY_FORM} onCancel={() => setCreating(false)} onSubmit={onCreate} />
      )}
      {editing && (
        <ScheduleForm
          initial={{ ...editing, _editing: true }}
          onCancel={() => setEditing(null)}
          onSubmit={onUpdate}
        />
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>
      ) : schedules.length === 0 ? (
        <div className="text-sm text-gray-500 italic">No schedules yet. Create one to run a task on a cron schedule.</div>
      ) : (
        <ul className="space-y-2">
          {schedules.map((s) => (
            <li key={s.id} className="border border-gray-200 dark:border-gray-700 rounded p-3 bg-white dark:bg-gray-800 flex items-center gap-3">
              <span className={s.enabled ? 'text-emerald-500' : 'text-gray-400'} title={s.enabled ? 'Enabled' : 'Disabled'}>
                <Clock className="w-4 h-4" />
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{s.name}</div>
                <div className="text-xs text-gray-500 font-mono">{s.cron_expr}</div>
                <div className="text-[10px] text-gray-400">
                  next: {new Date(s.next_fire_at).toLocaleString()} · fired {s.fire_count}× · failed {s.fail_count}×
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button title="Fire now" onClick={() => onFireNow(s.id)} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                  <Play className="w-3.5 h-3.5" />
                </button>
                <button
                  title={s.enabled ? 'Disable' : 'Enable'}
                  onClick={() => onToggle(s)}
                  className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  {s.enabled ? <Power className="w-3.5 h-3.5 text-emerald-500" /> : <PowerOff className="w-3.5 h-3.5 text-gray-400" />}
                </button>
                <button title="Edit" onClick={() => setEditing(s)} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button title="Delete" onClick={() => onDelete(s.id)} className="p-1 rounded hover:bg-rose-100 text-rose-500">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default SchedulesPage;
