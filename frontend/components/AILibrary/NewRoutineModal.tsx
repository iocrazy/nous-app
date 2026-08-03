// frontend/components/AILibrary/NewRoutineModal.tsx
// B6 — scheduling an agent in plain language (spec 2026-08-02 §B6).
//
// The inline routine form asked for a raw cron expression, which turns
// "every weekday morning" into a syntax question. This asks for the task in
// words, a frequency from a short list, and a time; the cron is derived
// (routineForm.ts). Custom still exposes the raw field for the cases the
// presets don't cover.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { schedulesService } from '../../services/schedulesService';
import { UiSelect } from '../ui';
import {
  FREQUENCY_PRESETS,
  buildRoutinePayload,
  emptyRoutineForm,
  validateRoutineForm,
  type RoutineFormError,
  type RoutineFormValues,
} from './routineForm';

interface NewRoutineModalProps {
  agent: AILibraryAgent;
  onClose: () => void;
  onCreated: () => void;
}

export const NewRoutineModal: React.FC<NewRoutineModalProps> = ({
  agent,
  onClose,
  onCreated,
}) => {
  const { t } = useTranslation();
  const [form, setForm] = useState<RoutineFormValues>(emptyRoutineForm);
  const [submitting, setSubmitting] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errors = validateRoutineForm(form);
  const hasError = (e: RoutineFormError) => showErrors && errors.includes(e);
  const update = <K extends keyof RoutineFormValues>(
    key: K,
    value: RoutineFormValues[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (errors.length > 0) {
      setShowErrors(true);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await schedulesService.create(buildRoutinePayload(form, agent.slug));
      onCreated();
    } catch (err) {
      console.error('[NewRoutineModal] create failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const fieldClass = (bad: boolean) =>
    `mt-1 w-full rounded-md border bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:outline-none ${
      bad ? 'border-danger-line' : 'border-ink-700 focus:border-[var(--accent-border)]'
    }`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-ink-800 bg-ink-900 p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-ink-100">
          {t('aiLibrary.agents.routines.newTitle', 'New routine')}
        </h2>
        <p className="mt-1 text-xs text-ink-500">
          {t(
            'aiLibrary.agents.routines.newHint',
            '{{name}} runs this on a schedule and replies on the issue it creates.',
            { name: agent.name },
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.routines.nameLabel', 'Name')}
            </label>
            <input
              type="text"
              value={form.name}
              maxLength={200}
              onChange={(e) => update('name', e.target.value)}
              placeholder={t(
                'aiLibrary.agents.routines.namePlaceholder',
                'Daily digest',
              )}
              className={fieldClass(hasError('name'))}
              disabled={submitting}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.routines.promptLabel', 'What should it do?')}
            </label>
            <textarea
              value={form.promptMd}
              rows={4}
              onChange={(e) => update('promptMd', e.target.value)}
              placeholder={t(
                'aiLibrary.agents.routines.promptPlaceholder',
                'Describe the task in your own words, as if briefing a colleague.',
              )}
              className={fieldClass(hasError('promptMd'))}
              disabled={submitting}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.routines.frequencyLabel', 'How often')}
            </label>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {FREQUENCY_PRESETS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => update('frequency', p.key)}
                  data-testid={`freq-${p.key}`}
                  aria-pressed={form.frequency === p.key}
                  className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                    form.frequency === p.key
                      ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
                      : 'border-ink-700 text-ink-400 hover:text-ink-200'
                  }`}
                  disabled={submitting}
                >
                  {t(p.labelKey, p.label)}
                </button>
              ))}
            </div>

            {form.frequency === 'custom' ? (
              <input
                type="text"
                value={form.customCron}
                onChange={(e) => update('customCron', e.target.value)}
                placeholder="*/30 8-18 * * *"
                className={`${fieldClass(hasError('customCron'))} font-mono`}
                disabled={submitting}
              />
            ) : (
              <div className="mt-2 flex items-center gap-2">
                <input
                  type="time"
                  value={`${form.hour}:${form.minute}`}
                  onChange={(e) => {
                    const [h, m] = e.target.value.split(':');
                    setForm((f) => ({ ...f, hour: h ?? f.hour, minute: m ?? f.minute }));
                  }}
                  aria-label={t('aiLibrary.agents.routines.timeLabel', 'Time')}
                  className="rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100"
                  disabled={submitting || form.frequency === 'hourly'}
                />
                <span className="text-[11px] text-ink-600">
                  {form.frequency === 'hourly'
                    ? t(
                        'aiLibrary.agents.routines.hourlyHint',
                        'Runs every hour at minute {{minute}}',
                        { minute: form.minute },
                      )
                    : form.timezone}
                </span>
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.routines.policyLabel', 'If a run is still active')}
            </label>
            <UiSelect
              value={form.deliveryPolicy}
              onChange={(e) =>
                update(
                  'deliveryPolicy',
                  e.target.value as RoutineFormValues['deliveryPolicy'],
                )
              }
              className="mt-1 w-full"
              disabled={submitting}
            >
              <option value="skip_if_active">
                {t('aiLibrary.agents.routines.policySkip', 'Skip this run')}
              </option>
              <option value="always">
                {t('aiLibrary.agents.routines.policyAlways', 'Start it anyway')}
              </option>
            </UiSelect>
          </div>

          <p className="rounded-md border border-ink-800 bg-ink-800/40 px-3 py-2 text-[11px] text-ink-500">
            {t(
              'aiLibrary.agents.routines.resultHint',
              'Each run creates an issue assigned to this agent; its answer arrives as a reply there.',
            )}
          </p>

          {error && (
            <div className="rounded-md border border-danger-line bg-danger-soft p-2 text-xs text-danger">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md border border-ink-700 px-4 py-2 text-sm text-ink-300 hover:bg-ink-800"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-md btn-tint-indigo px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting
                ? t('common.saving', 'Creating...')
                : t('common.create', 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default NewRoutineModal;
