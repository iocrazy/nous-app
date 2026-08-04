// frontend/components/AILibrary/AgentPersonaTab.tsx
// B2 — "who this agent is" (spec 2026-08-02 §B2).
//
// Absorbs four of the old eight sub-tabs (Overview / Files / Skills /
// Permissions). The three persona documents switch in place instead of
// stacking, because you edit one voice at a time; the attributes, skill
// bindings and permissions sit under them as the settings for that voice.
//
// Editing state is owned by AgentEditor — this component is presentational
// apart from which document is on screen. The header Save button covers the
// draft + skill bindings; permissions keep their own Save (a separate
// endpoint with its own role gate).

import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import type {
  AgentChatPermissions,
  AILibraryAgent,
  AILibrarySkill,
} from '../../types';
import { MarkdownEditor } from './MarkdownEditor';
import { AgentIconPicker } from './AgentIconPicker';
import PermissionsSection from './PermissionsSection';
import { renderModelSelect, type ProviderModelGroup } from './agentEditorModel';

type PersonaDoc = 'identity_md' | 'soul_md' | 'agent_md';

const PERSONA_DOCS: {
  key: PersonaDoc;
  file: string;
  labelKey: string;
  label: string;
}[] = [
  { key: 'identity_md', file: 'IDENTITY.md', labelKey: 'aiLibrary.agents.identityTitle', label: 'Identity' },
  { key: 'soul_md', file: 'SOUL.md', labelKey: 'aiLibrary.agents.soulTitle', label: 'Soul' },
  { key: 'agent_md', file: 'AGENT.md', labelKey: 'aiLibrary.agents.instructionsTitle', label: 'Instructions' },
];

interface AgentPersonaTabProps {
  agent: AILibraryAgent;
  draft: Partial<AILibraryAgent>;
  updateDraft: <K extends keyof AILibraryAgent>(key: K, value: AILibraryAgent[K]) => void;
  readOnly: boolean;
  catalogLocked: boolean;
  modelGroups: ProviderModelGroup[];
  localSkillIds: number[];
  allSkills: AILibrarySkill[] | null;
  skillsLoading: boolean;
  onAddSkill: (id: number) => void;
  onRemoveSkill: (id: number) => void;
  onMoveSkill: (id: number, direction: -1 | 1) => void;
  permDraft: AgentChatPermissions;
  onPermChange: (next: AgentChatPermissions) => void;
  onSavePermissions: () => void;
  permSaving: boolean;
}

export const AgentPersonaTab: React.FC<AgentPersonaTabProps> = ({
  agent,
  draft,
  updateDraft,
  readOnly,
  catalogLocked,
  modelGroups,
  localSkillIds,
  allSkills,
  skillsLoading,
  onAddSkill,
  onRemoveSkill,
  onMoveSkill,
  permDraft,
  onPermChange,
  onSavePermissions,
  permSaving,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<PersonaDoc>('identity_md');

  return (
    <div className="space-y-6">
      {readOnly && (
        <div className="rounded-lg border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
          {t('aiLibrary.agents.presetReadOnly')}
        </div>
      )}

      {/* Two columns per spec §03: the persona documents are the work, the
          attributes / skills / permissions are the settings that describe
          them. They used to be one long stack, so reading a prompt meant
          scrolling past every knob first. */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-[1.5fr_1fr] md:items-start">
      {/* Persona documents — one at a time. */}
      <section>
        <div className="mb-2 flex gap-1">
          {PERSONA_DOCS.map((d) => (
            <button
              key={d.key}
              type="button"
              onClick={() => setDoc(d.key)}
              data-testid={`persona-doc-${d.key}`}
              aria-pressed={doc === d.key}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                doc === d.key
                  ? 'bg-ink-800 text-ink-100'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              {t(d.labelKey, d.label)}
              <span className="ml-1.5 font-mono text-[10px] text-ink-600">{d.file}</span>
            </button>
          ))}
        </div>
        <MarkdownEditor
          value={draft[doc] ?? ''}
          onChange={(v) => updateDraft(doc, v)}
          disabled={readOnly}
          rows={doc === 'agent_md' ? 16 : 10}
        />
      </section>

      <div className="flex flex-col gap-6">
      {/* Attributes */}
      <section className="space-y-4 text-sm">
        <h3 className="text-sm font-semibold text-ink-200">
          {t('aiLibrary.agents.attributes', 'Attributes')}
        </h3>

        <div className="flex items-center gap-3">
          <AgentIconPicker
            value={draft.icon ?? null}
            onChange={(iconSlug) => updateDraft('icon', iconSlug)}
            disabled={catalogLocked}
            size={24}
          />
          <p className="text-xs text-ink-500">
            {t('aiLibrary.agents.iconHint', 'Icon shown in the sidebar and throughout the app.')}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.nameLabel', 'Name')}
            </label>
            <input
              type="text"
              value={draft.name ?? ''}
              onChange={(e) => updateDraft('name', e.target.value)}
              disabled={catalogLocked}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-[var(--accent-border)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.slugLabel', 'Slug')}
            </label>
            <input
              type="text"
              value={agent.slug}
              disabled
              className="mt-1 w-full cursor-not-allowed rounded-md border border-ink-800 bg-ink-950 px-3 py-2 font-mono text-sm text-ink-400"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.descriptionLabel', 'Description')}
          </label>
          <textarea
            value={draft.description ?? ''}
            onChange={(e) => updateDraft('description', e.target.value)}
            disabled={catalogLocked}
            rows={2}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-[var(--accent-border)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.modelLabel', 'Model')}
          </label>
          {renderModelSelect({
            value: draft.model ?? '',
            groups: modelGroups,
            disabled: readOnly,
            onChange: (v) => updateDraft('model', v),
            providerNotEnabledLabel: t(
              'aiLibrary.agents.modelProviderNotEnabled',
              'provider not enabled',
            ),
            noModelsLabel: t('aiLibrary.agents.noModelsAvailable'),
          })}
          {modelGroups.every((g) => g.models.length === 0) && (
            <button
              type="button"
              onClick={() => navigate('/settings?tab=ai')}
              className="mt-1.5 text-xs text-warn hover:underline"
            >
              {t('aiLibrary.agents.goToAISettings', 'Go to AI Settings')} →
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.temperatureLabel', 'Temperature')}
            </label>
            <input
              type="number"
              step="0.1"
              min={0}
              max={2}
              value={draft.temperature ?? 0}
              onChange={(e) => {
                // One decimal, so 0.7 doesn't round-trip as 0.6999999...
                const raw = Number(e.target.value);
                updateDraft(
                  'temperature',
                  Number.isFinite(raw) ? Math.round(raw * 10) / 10 : 0,
                );
              }}
              disabled={readOnly}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-[var(--accent-border)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.maxTokensLabel', 'Max tokens')}
            </label>
            <input
              type="number"
              min={1}
              max={100000}
              value={draft.max_tokens ?? 0}
              onChange={(e) => updateDraft('max_tokens', Number(e.target.value))}
              disabled={readOnly}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-[var(--accent-border)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-400">
              {t('aiLibrary.agents.enabledLabel', 'Enabled')}
            </label>
            <label className="mt-1 flex items-center gap-2 rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100">
              <input
                type="checkbox"
                checked={draft.enabled ?? false}
                onChange={(e) => updateDraft('enabled', e.target.checked)}
                disabled={catalogLocked}
                className="h-4 w-4"
              />
              <span>
                {(draft.enabled ?? false) ? t('common.yes', 'Yes') : t('common.no', 'No')}
              </span>
            </label>
          </div>
        </div>
      </section>

      {/* Skills */}
      <section>
        <SkillsSection
          localSkillIds={localSkillIds}
          allSkills={allSkills}
          skillsLoading={skillsLoading}
          readOnly={catalogLocked}
          onAdd={onAddSkill}
          onRemove={onRemoveSkill}
          onMove={onMoveSkill}
        />
      </section>

      {/* Permissions — own Save, own endpoint. */}
      <section>
        <PermissionsSection value={permDraft} onChange={onPermChange} />
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            disabled={permSaving}
            onClick={onSavePermissions}
            className="rounded-lg btn-tint-indigo px-4 py-2 text-sm font-medium"
          >
            {permSaving ? t('common.saving') : t('common.save', 'Save')}
          </button>
        </div>
      </section>
      </div>
      </div>
    </div>
  );
};

/**
 * Skills sub-tab body — renders the bound skill list (with reorder + remove)
 * plus the "Available Skills" picker underneath. All mutations flow through
 * local state (``localSkillIds``); the PATCH is triggered by the header Save
 * button, which reads both the Overview draft and the skill ids at save time.
 *
 * Read-only mode (system preset + non-admin viewer) hides every action button
 * but still shows the bound list so the viewer can see what's composed.
 */
const SkillsSection: React.FC<{
  localSkillIds: number[];
  allSkills: AILibrarySkill[] | null;
  skillsLoading: boolean;
  readOnly: boolean;
  onAdd: (skillId: number) => void;
  onRemove: (skillId: number) => void;
  onMove: (skillId: number, direction: -1 | 1) => void;
}> = ({ localSkillIds, allSkills, skillsLoading, readOnly, onAdd, onRemove, onMove }) => {
  const { t } = useTranslation();

  // Build a quick lookup so we can render skill metadata for the bound list
  // without scanning `allSkills` on every row.
  const skillById = useMemo(() => {
    const map = new Map<number, AILibrarySkill>();
    (allSkills ?? []).forEach((s) => map.set(s.id, s));
    return map;
  }, [allSkills]);

  // Bound list preserves the user-chosen order (== sort_order on save).
  const boundSkills = useMemo(
    () =>
      localSkillIds.map((id) => ({
        id,
        skill: skillById.get(id) ?? null,
      })),
    [localSkillIds, skillById],
  );

  // Available list = every accessible skill minus the ones already bound.
  // Stable order = the server order from ``listSkills`` (name asc with preset
  // grouping today).
  const availableSkills = useMemo(() => {
    if (!allSkills) return [];
    const bound = new Set(localSkillIds);
    return allSkills.filter((s) => !bound.has(s.id));
  }, [allSkills, localSkillIds]);

  if (skillsLoading && allSkills === null) {
    return <p className="text-sm text-ink-500">{t('aiLibrary.agents.loadingSkills')}</p>;
  }

  return (
    <section className="space-y-6">
      {readOnly && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-warn">
          {t('aiLibrary.agents.presetReadOnly')}
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold text-ink-200">
          {t('aiLibrary.agents.currentSkills', 'Bound Skills')}
          <span className="ml-2 text-xs font-normal text-ink-500">
            ({boundSkills.length})
          </span>
        </h3>
        {boundSkills.length === 0 ? (
          <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-3 py-4 text-sm text-ink-500">
            {t(
              'aiLibrary.agents.noSkillsBound',
              'No skills bound yet. Add one below.',
            )}
          </div>
        ) : (
          <ul className="space-y-1.5">
            {boundSkills.map(({ id, skill }, idx) => (
              <li
                key={id}
                className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-900/60 px-3 py-2"
              >
                <span className="text-xl leading-none" aria-hidden>
                  {skill?.icon ?? ''}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-ink-100">
                    {skill?.name ?? `Skill #${id}`}
                  </div>
                  {skill?.slug && (
                    <div className="truncate font-mono text-xs text-ink-500">
                      {skill.slug}
                    </div>
                  )}
                </div>
                {skill && <SkillScopeBadge skill={skill} />}
                {!readOnly && (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onMove(id, -1)}
                      disabled={idx === 0}
                      className="rounded-md border border-ink-700 bg-ink-800 p-1.5 text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
                      title={t('aiLibrary.agents.moveUp', 'Move up')}
                      aria-label={t('aiLibrary.agents.moveUp', 'Move up')}
                    >
                      <ArrowUp size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onMove(id, 1)}
                      disabled={idx === boundSkills.length - 1}
                      className="rounded-md border border-ink-700 bg-ink-800 p-1.5 text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
                      title={t('aiLibrary.agents.moveDown', 'Move down')}
                      aria-label={t('aiLibrary.agents.moveDown', 'Move down')}
                    >
                      <ArrowDown size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onRemove(id)}
                      className="rounded-md border border-red-500/30 bg-red-500/10 p-1.5 text-red-300 hover:bg-red-500/20"
                      title={t('aiLibrary.agents.removeSkill', 'Remove')}
                      aria-label={t('aiLibrary.agents.removeSkill', 'Remove')}
                    >
                      <X size={14} />
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {!readOnly && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-ink-200">
            {t('aiLibrary.agents.availableSkills', 'Available Skills')}
            <span className="ml-2 text-xs font-normal text-ink-500">
              ({availableSkills.length})
            </span>
          </h3>
          {availableSkills.length === 0 ? (
            <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-3 py-4 text-sm text-ink-500">
              {t(
                'aiLibrary.agents.noAvailableSkills',
                'No available skills. Create one in the Skills tab.',
              )}
            </div>
          ) : (
            <ul className="space-y-1.5">
              {availableSkills.map((skill) => (
                <li
                  key={skill.id}
                  className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2"
                >
                  <span className="text-xl leading-none" aria-hidden>
                    {skill.icon ?? ''}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-ink-100">
                      {skill.name}
                    </div>
                    {skill.slug && (
                      <div className="truncate font-mono text-xs text-ink-500">
                        {skill.slug}
                      </div>
                    )}
                  </div>
                  <SkillScopeBadge skill={skill} />
                  <button
                    type="button"
                    onClick={() => onAdd(skill.id)}
                    className="inline-flex items-center gap-1 rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-xs font-medium text-[var(--accent-text)] hover:bg-[var(--accent-soft)]"
                  >
                    <Plus size={12} />
                    {t('aiLibrary.agents.addSkill', 'Add')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};

/**
 * Compact scope indicator for the skill-binding rows. Mirrors the preset /
 * team / project / private pattern used elsewhere in the AI Library UI.
 */
const SkillScopeBadge: React.FC<{ skill: AILibrarySkill }> = ({ skill }) => {
  const { t } = useTranslation();
  const base = 'rounded border px-2 py-0.5 text-xs whitespace-nowrap';
  const isPreset =
    skill.is_public && skill.team_id == null && skill.project_id == null;

  if (isPreset) {
    return (
      <span className={`${base} border-ink-700 bg-ink-800 text-ink-300`}>
        {t('aiLibrary.officialTemplate', 'Official template')}
      </span>
    );
  }
  if (skill.team_id != null) {
    return (
      <span className={`${base} border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]`}>
        {t('aiLibrary.skills.scopeBadgeTeam', 'Team: {{name}}', {
          name: skill.team_name ?? skill.team_id,
        })}
      </span>
    );
  }
  if (skill.project_id != null) {
    return (
      <span className={`${base} border-emerald-500/40 bg-emerald-500/10 text-emerald-300`}>
        {t('aiLibrary.skills.scopeBadgeProject', 'Project: {{name}}', {
          name: skill.project_name ?? skill.project_id,
        })}
      </span>
    );
  }
  return (
    <span className={`${base} border-ink-700 bg-ink-900 text-ink-400`}>
      {t('aiLibrary.skills.scopeBadgePrivate', 'Private')}
    </span>
  );
};
export default AgentPersonaTab;
