// frontend/components/AILibrary/NewSkillModal.tsx
// Modal form for creating a new user-owned skill.
//
// Mirrors NewAgentModal: optional fork from an existing skill, optional
// scope — Private / Team / Project — where:
//   - Private (default): skill is visible only to the creator
//   - Team:    visible to all members of the chosen team
//   - Project: visible to owner + members of the chosen project

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibrarySkill, Team, Project } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { fetchMyTeams } from '../../services/teamService';
import { fetchProjects } from '../../services/projectsService';

interface NewSkillModalProps {
  existingSkills: AILibrarySkill[];
  onClose: () => void;
  onCreated: (slug: string) => void;
  /**
   * Preselect a source skill to fork from. When set, the "Fork from" dropdown
   * opens with this value already selected. Reserved for a future
   * SkillEditor fork button.
   */
  initialForkFrom?: string;
}

type ScopeKind = 'private' | 'team' | 'project';

const SLUG_PATTERN = /^[a-z0-9_-]+$/;

export const NewSkillModal: React.FC<NewSkillModalProps> = ({
  existingSkills,
  onClose,
  onCreated,
  initialForkFrom,
}) => {
  const { t } = useTranslation();
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [icon, setIcon] = useState('');
  const [forkFrom, setForkFrom] = useState<string>(initialForkFrom ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Scope picker state — default private (no team / no project).
  const [scopeKind, setScopeKind] = useState<ScopeKind>('private');
  const [teamId, setTeamId] = useState<string>('');
  const [projectId, setProjectId] = useState<string>('');
  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [scopeLoading, setScopeLoading] = useState(false);

  // Lazy-fetch teams + projects the first time the user leaves "Private".
  useEffect(() => {
    if (scopeKind === 'private') return;
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
        console.error('[NewSkillModal] load scopes failed:', err);
      })
      .finally(() => {
        if (!cancelled) setScopeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeKind, teams.length, projects.length]);

  const slugIsValid = slug.length > 0 && SLUG_PATTERN.test(slug);
  const scopeIsValid =
    scopeKind === 'private' ||
    (scopeKind === 'team' && teamId !== '') ||
    (scopeKind === 'project' && projectId !== '');
  const canSubmit =
    slugIsValid && name.trim().length > 0 && scopeIsValid && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const parsedTeamId =
        scopeKind === 'team' && teamId ? Number(teamId) : undefined;
      const parsedProjectId =
        scopeKind === 'project' && projectId ? Number(projectId) : undefined;
      const created = await aiLibraryService.createSkill({
        slug,
        name: name.trim(),
        description: description.trim() || undefined,
        category: category.trim() || undefined,
        icon: icon.trim() || undefined,
        fork_from: forkFrom || undefined,
        team_id: parsedTeamId,
        project_id: parsedProjectId,
      });
      onCreated(created.slug ?? String(created.id));
    } catch (err) {
      console.error('[NewSkillModal] createSkill failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-ink-800 bg-ink-900 p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-ink-100">
          {t('aiLibrary.skills.newSkillTitle', 'New Skill')}
        </h2>
        <p className="mt-1 text-xs text-ink-500">
          {t(
            'aiLibrary.skills.newSkillHint',
            'Create a custom skill. You can start from scratch or fork an existing skill as a starting point.',
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.skills.slugLabel', 'Slug')}
            </label>
            <input
              type="text"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-custom-skill"
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
              required
            />
            {slug && !slugIsValid && (
              <p className="mt-1 text-xs text-red-400">
                {t(
                  'aiLibrary.skills.slugInvalid',
                  'Lowercase letters, digits, dash, underscore only.',
                )}
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.skills.nameLabel', 'Name')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Custom Skill"
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.skills.descriptionLabel', 'Description')}
              <span className="ml-1 text-ink-600">
                ({t('common.optional', 'optional')})
              </span>
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-ink-400">
                {t('aiLibrary.skills.categoryLabel', 'Category')}
                <span className="ml-1 text-ink-600">
                  ({t('common.optional', 'optional')})
                </span>
              </label>
              <input
                type="text"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="writing"
                className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
                disabled={submitting}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-ink-400">
                {t('aiLibrary.skills.iconLabel', 'Icon')}
                <span className="ml-1 text-ink-600">
                  ({t('common.optional', 'optional')})
                </span>
              </label>
              <input
                type="text"
                value={icon}
                onChange={(e) => setIcon(e.target.value)}
                placeholder="✨"
                maxLength={4}
                className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
                disabled={submitting}
              />
            </div>
          </div>

          <fieldset className="space-y-2">
            <legend className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.skills.scopeLabel', 'Scope')}
            </legend>
            <div className="flex gap-2">
              <ScopeRadio
                checked={scopeKind === 'private'}
                disabled={submitting}
                onChange={() => setScopeKind('private')}
                label={t('aiLibrary.skills.scopePrivateLabel', 'Private')}
              />
              <ScopeRadio
                checked={scopeKind === 'team'}
                disabled={submitting}
                onChange={() => setScopeKind('team')}
                label={t('aiLibrary.skills.scopeTeamLabel', 'Team')}
              />
              <ScopeRadio
                checked={scopeKind === 'project'}
                disabled={submitting}
                onChange={() => setScopeKind('project')}
                label={t('aiLibrary.skills.scopeProjectLabel', 'Project')}
              />
            </div>
            {scopeKind === 'team' && (
              <select
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
                disabled={submitting || scopeLoading}
                required
              >
                <option value="">
                  {scopeLoading
                    ? t('common.loading', 'Loading...')
                    : t('aiLibrary.skills.scopeTeamPicker', 'Select a team')}
                </option>
                {teams.map((tm) => (
                  <option key={tm.id} value={tm.id}>
                    {tm.name}
                  </option>
                ))}
              </select>
            )}
            {scopeKind === 'project' && (
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
                disabled={submitting || scopeLoading}
                required
              >
                <option value="">
                  {scopeLoading
                    ? t('common.loading', 'Loading...')
                    : t('aiLibrary.skills.scopeProjectPicker', 'Select a project')}
                </option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
            <p className="text-xs text-ink-500">
              {t(
                'aiLibrary.skills.scopeHint',
                'Private = only you. Team / Project = everyone in that scope.',
              )}
            </p>
          </fieldset>

          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.skills.forkFromLabel', 'Fork from')}
              <span className="ml-1 text-ink-600">
                ({t('common.optional', 'optional')})
              </span>
            </label>
            <select
              value={forkFrom}
              onChange={(e) => setForkFrom(e.target.value)}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none"
              disabled={submitting}
            >
              <option value="">
                {t('aiLibrary.skills.forkFromNone', 'Start from scratch')}
              </option>
              {existingSkills
                .filter((s) => s.slug)
                .map((s) => (
                  <option key={s.slug} value={s.slug}>
                    {s.name} {s.is_public && !s.project_id ? '(preset)' : ''}
                  </option>
                ))}
            </select>
            <p className="mt-1 text-xs text-ink-500">
              {t(
                'aiLibrary.skills.forkFromHint',
                'Copies the SKILL.md body + metadata from the source. Sub-files (references/, scripts/, assets/) are NOT copied.',
              )}
            </p>
          </div>

          {error && (
            <div className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
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
              disabled={!canSubmit}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
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

/**
 * Small radio chip used by the Scope picker. Mirrors the same helper in
 * NewAgentModal; kept inline to keep the file self-contained.
 */
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
        ? 'border-indigo-500 bg-indigo-500/10 text-indigo-200'
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

export default NewSkillModal;
