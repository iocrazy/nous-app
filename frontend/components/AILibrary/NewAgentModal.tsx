// frontend/components/AILibrary/NewAgentModal.tsx
// New Agent — template first (B5, spec 2026-08-02 §B5).
//
// The form used to lead with slug/name and bury "Fork from" at the bottom as
// an optional dropdown, which reads as "blank agent is the normal path". It
// isn't: starting from an official template and customizing it is. So step 1
// is picking a template (or explicitly choosing Blank), and identity comes
// after.
//
// Form rules live in newAgentForm.ts so the scope validation and the
// BIGINT-as-string handling are testable without a DOM.

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent, Team, Project } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { fetchMyTeams } from '../../services/teamService';
import { fetchProjects } from '../../services/projectsService';
import { UiSelect } from '../ui';
import { getAgentIcon } from './agentIcons';
import {
  buildCreatePayload,
  emptyForm,
  slugFromName,
  validateForm,
  type NewAgentForm,
  type NewAgentFormError,
  type NewAgentScope,
} from './newAgentForm';

interface NewAgentModalProps {
  existingAgents: AILibraryAgent[];
  onClose: () => void;
  onCreated: (slug: string) => void;
  /** Preselect the template to fork. Used by the gallery's Fork button. */
  initialForkFrom?: string;
}

export const NewAgentModal: React.FC<NewAgentModalProps> = ({
  existingAgents,
  onClose,
  onCreated,
  initialForkFrom,
}) => {
  const { t } = useTranslation();
  const [form, setForm] = useState<NewAgentForm>(() =>
    emptyForm(initialForkFrom ?? null),
  );
  /** Whether the user typed their own slug — stop deriving it if so. */
  const [slugTouched, setSlugTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [scopeLoading, setScopeLoading] = useState(false);

  const update = <K extends keyof NewAgentForm>(key: K, value: NewAgentForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const templates = useMemo(
    () => existingAgents.filter((a) => a.is_system_preset),
    [existingAgents],
  );
  const takenSlugs = useMemo(
    () => existingAgents.map((a) => a.slug),
    [existingAgents],
  );
  const errors = validateForm(form, takenSlugs);
  const hasError = (e: NewAgentFormError) => showErrors && errors.includes(e);

  // Lazy-fetch teams + projects the first time the user leaves "Private".
  useEffect(() => {
    if (form.scope === 'private') return;
    if (teams.length > 0 || projects.length > 0) return;
    let cancelled = false;
    setScopeLoading(true);
    Promise.all([fetchMyTeams(), fetchProjects()])
      .then(([teamList, projectList]) => {
        if (cancelled) return;
        setTeams(teamList);
        setProjects(projectList);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[NewAgentModal] load scopes failed:', err);
      })
      .finally(() => {
        if (!cancelled) setScopeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [form.scope, teams.length, projects.length]);

  const pickTemplate = (slug: string | null) => {
    setForm((f) => {
      const source = slug ? existingAgents.find((a) => a.slug === slug) : null;
      // Prefill from the template only while the user hasn't typed a name —
      // switching templates shouldn't clobber something they wrote.
      const name = f.name || (source ? `${source.name} (copy)` : '');
      return {
        ...f,
        template: slug,
        name,
        slug: slugTouched ? f.slug : slugFromName(name),
      };
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (errors.length > 0) {
      setShowErrors(true);
      return;
    }
    setSubmitting(true);
    setError(null);
    setWarning(null);
    try {
      const created = await aiLibraryService.createAgent(buildCreatePayload(form));

      // The backend's fork copies content but NOT skill bindings. Copy them
      // in a follow-up PATCH so a forked agent actually behaves like its
      // template. If that second call fails the agent still exists, so this
      // reports a warning and continues — never a silent partial fork.
      const source = form.template
        ? existingAgents.find((a) => a.slug === form.template)
        : undefined;
      if (source && (source.skill_ids?.length ?? 0) > 0) {
        try {
          await aiLibraryService.updateAgent(created.slug, {
            skill_ids: source.skill_ids,
          });
        } catch (err) {
          console.error('[NewAgentModal] copying skill bindings failed:', err);
          setWarning(
            t(
              'aiLibrary.agents.forkSkillsNotCopied',
              'Agent created, but its skills were not copied — bind them manually.',
            ),
          );
          onCreated(created.slug);
          return;
        }
      }
      onCreated(created.slug);
    } catch (err) {
      console.error('[NewAgentModal] createAgent failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass = (bad: boolean) =>
    `mt-1 w-full rounded-md border bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:outline-none ${
      bad ? 'border-danger-line' : 'border-ink-700 focus:border-[var(--accent-border)]'
    }`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-ink-800 bg-ink-900 p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-ink-100">
          {t('aiLibrary.agents.newAgentTitle', 'New Agent')}
        </h2>
        <p className="mt-1 text-xs text-ink-500">
          {t(
            'aiLibrary.agents.newAgentHintV2',
            'Start from an official template and customize it, or build from blank.',
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-5">
          {/* Step 1 — template */}
          <fieldset>
            <legend className="text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.templateStep', '1. Start from')}
            </legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              <TemplateCard
                selected={form.template === null}
                title={t('aiLibrary.agents.blankTemplate', 'Blank agent')}
                subtitle={t('aiLibrary.agents.blankTemplateHint', 'Write it yourself')}
                onSelect={() => pickTemplate(null)}
                testId="template-blank"
              />
              {templates.map((a) => {
                const Icon = getAgentIcon(a.icon);
                return (
                  <TemplateCard
                    key={a.slug}
                    selected={form.template === a.slug}
                    title={a.name}
                    subtitle={a.description ?? a.model}
                    icon={<Icon size={14} />}
                    onSelect={() => pickTemplate(a.slug)}
                    testId={`template-${a.slug}`}
                  />
                );
              })}
            </div>
            {form.template && (
              <p className="mt-1.5 text-xs text-ink-500">
                {t(
                  'aiLibrary.agents.forkFromHintV2',
                  'Copies the identity / soul / instructions, model settings and skill bindings.',
                )}
              </p>
            )}
          </fieldset>

          {/* Step 2 — identity */}
          <fieldset>
            <legend className="text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.identityStep', '2. Name it')}
            </legend>
            <div className="mt-2 grid gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-xs font-medium text-ink-400">
                  {t('aiLibrary.agents.nameLabel', 'Name')}
                </label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => {
                    const name = e.target.value;
                    setForm((f) => ({
                      ...f,
                      name,
                      slug: slugTouched ? f.slug : slugFromName(name),
                    }));
                  }}
                  placeholder="My Custom Agent"
                  className={inputClass(hasError('name'))}
                  disabled={submitting}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-ink-400">
                  {t('aiLibrary.agents.slugLabel', 'Slug')}
                </label>
                <input
                  type="text"
                  value={form.slug}
                  onChange={(e) => {
                    setSlugTouched(true);
                    update('slug', e.target.value);
                  }}
                  placeholder="my-custom-agent"
                  className={`${inputClass(hasError('slug') || hasError('slugTaken'))} font-mono`}
                  disabled={submitting}
                />
                {hasError('slug') && (
                  <p className="mt-1 text-xs text-danger">
                    {t(
                      'aiLibrary.agents.slugInvalid',
                      'Lowercase letters, digits, dash, underscore only.',
                    )}
                  </p>
                )}
                {hasError('slugTaken') && (
                  <p className="mt-1 text-xs text-danger">
                    {t('aiLibrary.agents.slugTaken', 'That slug is already in use.')}
                  </p>
                )}
              </div>
            </div>
            <div className="mt-3">
              <label className="block text-xs font-medium text-ink-400">
                {t('aiLibrary.agents.descriptionLabel', 'Description')}
                <span className="ml-1 text-ink-600">
                  ({t('common.optional', 'optional')})
                </span>
              </label>
              <input
                type="text"
                value={form.description}
                onChange={(e) => update('description', e.target.value)}
                className={inputClass(false)}
                disabled={submitting}
              />
            </div>
          </fieldset>

          {/* Step 3 — scope */}
          <fieldset>
            <legend className="text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.scopeStep', '3. Who can use it')}
            </legend>
            <div className="mt-2 flex gap-2">
              {(['private', 'team', 'project'] as NewAgentScope[]).map((k) => (
                <ScopeRadio
                  key={k}
                  checked={form.scope === k}
                  disabled={submitting}
                  onChange={() => update('scope', k)}
                  label={t(
                    `aiLibrary.agents.scope${k[0].toUpperCase()}${k.slice(1)}Label`,
                    k[0].toUpperCase() + k.slice(1),
                  )}
                />
              ))}
            </div>
            {form.scope === 'team' && (
              <UiSelect
                value={form.teamId}
                onChange={(e) => update('teamId', e.target.value)}
                className="mt-2 w-full"
                disabled={submitting || scopeLoading}
              >
                <option value="">
                  {scopeLoading
                    ? t('common.loading', 'Loading...')
                    : t('aiLibrary.agents.scopeTeamPicker', 'Select a team')}
                </option>
                {teams.map((tm) => (
                  <option key={tm.id} value={tm.id}>
                    {tm.name}
                  </option>
                ))}
              </UiSelect>
            )}
            {form.scope === 'project' && (
              <UiSelect
                value={form.projectId}
                onChange={(e) => update('projectId', e.target.value)}
                className="mt-2 w-full"
                disabled={submitting || scopeLoading}
              >
                <option value="">
                  {scopeLoading
                    ? t('common.loading', 'Loading...')
                    : t('aiLibrary.agents.scopeProjectPicker', 'Select a project')}
                </option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </UiSelect>
            )}
            {(hasError('teamId') || hasError('projectId')) && (
              <p className="mt-1 text-xs text-danger">
                {t(
                  'aiLibrary.agents.scopeIdRequired',
                  'Pick one, or the agent is created as private.',
                )}
              </p>
            )}
            <p className="mt-1 text-xs text-ink-500">
              {t(
                'aiLibrary.agents.scopeHint',
                'Private = only you. Team / Project = everyone in that scope.',
              )}
            </p>
          </fieldset>

          {error && (
            <div className="rounded-md border border-danger-line bg-danger-soft p-2 text-xs text-danger">
              {error}
            </div>
          )}
          {warning && (
            <div className="rounded-md border border-warn-line bg-warn-soft p-2 text-xs text-warn">
              {warning}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
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

function TemplateCard({
  selected,
  title,
  subtitle,
  icon,
  onSelect,
  testId,
}: {
  selected: boolean;
  title: string;
  subtitle?: string | null;
  icon?: React.ReactNode;
  onSelect: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={testId}
      aria-pressed={selected}
      className={`rounded-lg border p-2.5 text-left transition-colors ${
        selected
          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
          : 'border-ink-700 bg-ink-800 hover:border-ink-600'
      }`}
    >
      <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink-100">
        {icon}
        <span className="min-w-0 truncate">{title}</span>
      </span>
      {subtitle && (
        <span className="mt-0.5 line-clamp-2 block text-[11px] text-ink-500">
          {subtitle}
        </span>
      )}
    </button>
  );
}

interface ScopeRadioProps {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: () => void;
}

const ScopeRadio: React.FC<ScopeRadioProps> = ({
  checked,
  disabled,
  label,
  onChange,
}) => (
  <label
    className={`inline-flex flex-1 cursor-pointer items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors ${
      checked
        ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'border-ink-700 bg-ink-800 text-ink-300 hover:bg-ink-750'
    } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
  >
    <input
      type="radio"
      className="h-3.5 w-3.5"
      checked={checked}
      disabled={disabled}
      onChange={onChange}
    />
    {label}
  </label>
);

export default NewAgentModal;
