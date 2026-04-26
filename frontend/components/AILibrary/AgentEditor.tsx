// frontend/components/AILibrary/AgentEditor.tsx
// Agent edit pane — Overview / Files / Skills sub-tabs.
//
// - Overview: editable form (name / description / model / temperature /
//   max_tokens / enabled). Disabled for system presets unless the user is admin.
// - Files: IDENTITY.md / SOUL.md / AGENT.md editors (preset = read-only).
// - Draft state is local; `save()` PATCHes via aiLibraryService and replaces
//   the hydrated agent immutably on success.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  AgentRunDetail,
  AgentRunListItem,
  AgentRunListResponse,
  AgentRunStatus,
  AILibraryAgent,
  AILibrarySkill,
  AISettings as AISettingsType,
} from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  GitFork,
  Play,
  Plus,
  RefreshCw,
  X,
} from 'lucide-react';
import { MarkdownEditor } from './MarkdownEditor';
import { NewAgentModal } from './NewAgentModal';
import { AgentIconPicker } from './AgentIconPicker';

type SubTab = 'overview' | 'files' | 'skills' | 'runs';

const RUNS_PAGE_SIZE = 25;
const RUNS_POLL_INTERVAL_MS = 10_000;

interface AgentEditorProps {
  slug: string;
  /**
   * Called after a successful fork. Parent should refresh its agent list and
   * (ideally) select the new slug so the user lands on their fresh copy.
   */
  onAgentForked?: (newSlug: string) => void;
}

export const AgentEditor: React.FC<AgentEditorProps> = ({ slug, onAgentForked }) => {
  const { t } = useTranslation();
  const { userProfile, aiSettings } = useAuth();
  const { addToast } = useToast();
  const isAdmin = userProfile.role === 'admin';
  const modelGroups = useMemo(() => getAvailableModels(aiSettings), [aiSettings]);
  const [agent, setAgent] = useState<AILibraryAgent | null>(null);
  const [sub, setSub] = useState<SubTab>('overview');
  const [draft, setDraft] = useState<Partial<AILibraryAgent>>({});
  const [localSkillIds, setLocalSkillIds] = useState<number[]>([]);
  const [allSkills, setAllSkills] = useState<AILibrarySkill[] | null>(null);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [allAgents, setAllAgents] = useState<AILibraryAgent[]>([]);

  useEffect(() => {
    let cancelled = false;
    setAgent(null);
    setError(null);
    setSub('overview');
    setLocalSkillIds([]);

    aiLibraryService
      .getAgent(slug)
      .then((a) => {
        if (cancelled) return;
        setAgent(a);
        setDraft(buildDraft(a));
        setLocalSkillIds(a.skill_ids);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentEditor] getAgent failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Lazily load the full skill catalog the first time the Skills tab is opened.
  // Cached on the editor instance — reused across tab switches until the agent
  // slug changes (which remounts the effect via the loadSkills dependency).
  useEffect(() => {
    // skillsLoading must NOT be a dep: setSkillsLoading(true) below would
    // retrigger this effect, run cleanup on the previous pass, flip
    // `cancelled=true`, and the in-flight fetch would then silently no-op in
    // both its .then and .finally — UI stuck on "Loading skills..." forever.
    if (sub !== 'skills' || allSkills !== null) return;
    let cancelled = false;
    setSkillsLoading(true);
    aiLibraryService
      .listSkills()
      .then((list) => {
        if (cancelled) return;
        setAllSkills(list);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentEditor] listSkills failed:', err);
        addToast(
          `Failed to load skills: ${err instanceof Error ? err.message : String(err)}`,
          'error',
        );
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sub, allSkills, addToast]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        Failed to load agent: {error}
      </div>
    );
  }

  if (!agent) {
    return <div className="text-sm text-zinc-500">Loading...</div>;
  }

  const isPreset = agent.is_system_preset;
  // Preset agents are read-only unless the caller is an admin.
  const readOnly = isPreset && !isAdmin;

  // Skills dirty check — structural compare of the ordered id array.
  const skillsDirty =
    JSON.stringify(localSkillIds) !== JSON.stringify(agent.skill_ids);

  const save = async (): Promise<void> => {
    if (readOnly) return;
    setSaving(true);
    setError(null);
    try {
      // Build the PATCH payload immutably from the overview/files draft, and
      // attach ``skill_ids`` only when the Skills tab has pending changes so
      // we don't wipe+rewrite bindings on unrelated saves.
      const patch: Partial<AILibraryAgent> = skillsDirty
        ? { ...draft, skill_ids: localSkillIds }
        : { ...draft };
      const updated = await aiLibraryService.updateAgent(slug, patch);
      setAgent(updated);
      setDraft(buildDraft(updated));
      setLocalSkillIds(updated.skill_ids);
      const message = skillsDirty
        ? t('aiLibrary.agents.skillsUpdatedToast', 'Skills updated')
        : t('aiLibrary.agents.saved', 'Agent saved');
      addToast(message, 'success');
    } catch (err) {
      console.error('[AgentEditor] updateAgent failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(`Failed to save agent: ${msg}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  /**
   * Resume from a paused state (budget or manual). Clears `paused_reason`
   * via a dedicated endpoint so the PATCH route's exclude-null semantics
   * stay uniform. If the agent is still over its monthly budget, the
   * sweeper will re-pause within ~60 s — callers should bump the budget
   * first to avoid the flap.
   */
  const handleResume = async (): Promise<void> => {
    if (readOnly) return;
    setResuming(true);
    setError(null);
    try {
      const updated = await aiLibraryService.resumeAgent(slug);
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(
        t('aiLibrary.agents.budget.resumedToast', 'Agent resumed'),
        'success',
      );
    } catch (err) {
      console.error('[AgentEditor] resumeAgent failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      addToast(`Failed to resume agent: ${msg}`, 'error');
    } finally {
      setResuming(false);
    }
  };

  // ─── Skill binding helpers (local-state only; PATCH on Save) ───────────────

  const addSkill = (skillId: number): void => {
    if (readOnly) return;
    setLocalSkillIds((ids) =>
      ids.includes(skillId) ? ids : [...ids, skillId],
    );
  };

  const removeSkill = (skillId: number): void => {
    if (readOnly) return;
    setLocalSkillIds((ids) => ids.filter((id) => id !== skillId));
  };

  const moveSkill = (skillId: number, direction: -1 | 1): void => {
    if (readOnly) return;
    setLocalSkillIds((ids) => {
      const idx = ids.indexOf(skillId);
      if (idx === -1) return ids;
      const next = idx + direction;
      if (next < 0 || next >= ids.length) return ids;
      const copy = [...ids];
      const [moved] = copy.splice(idx, 1);
      copy.splice(next, 0, moved);
      return copy;
    });
  };

  const updateDraft = <K extends keyof AILibraryAgent>(key: K, value: AILibraryAgent[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  /**
   * Open the fork modal. We lazily fetch the full agent list so the Fork-from
   * dropdown inside <NewAgentModal> has something to show if the user wants to
   * pick a different source after opening.
   */
  const openForkModal = async () => {
    setForkModalOpen(true);
    if (allAgents.length === 0) {
      try {
        const list = await aiLibraryService.listAgents();
        setAllAgents(list);
      } catch (err) {
        console.error('[AgentEditor] listAgents for fork failed:', err);
      }
    }
  };

  const handleForkCreated = (newSlug: string) => {
    setForkModalOpen(false);
    addToast(
      t('aiLibrary.agents.forkedToast', 'Forked as {{slug}}', { slug: newSlug }),
      'success',
    );
    onAgentForked?.(newSlug);
  };

  const subTabs: SubTab[] = ['overview', 'files', 'skills', 'runs'];

  return (
    <div>
      <header className="mb-4 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-zinc-100 truncate">{agent.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <span className="font-mono">{agent.slug}</span>
            <span>·</span>
            <span>{agent.model}</span>
            <ScopeBadge agent={agent} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isPreset && (
            <button
              onClick={openForkModal}
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-700 transition-colors whitespace-nowrap"
              title={t('aiLibrary.agents.forkAgent', 'Fork to My Agents')}
            >
              <GitFork size={14} />
              {t('aiLibrary.agents.forkAgent', 'Fork to My Agents')}
            </button>
          )}
          {!readOnly && (
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-indigo-500/10 border border-indigo-500/30 px-4 py-2 text-sm font-medium text-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
            >
              {saving ? 'Saving...' : t('aiLibrary.agents.saveChanges')}
            </button>
          )}
        </div>
      </header>

      <nav className="mb-4 flex gap-1 border-b border-zinc-800">
        {subTabs.map((k) => (
          <button
            key={k}
            onClick={() => setSub(k)}
            className={`px-3 py-2 text-sm font-medium transition-colors -mb-px border-b-2 ${
              sub === k
                ? 'border-indigo-500 text-zinc-100'
                : 'border-transparent text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {k[0].toUpperCase() + k.slice(1)}
          </button>
        ))}
      </nav>

      {sub === 'overview' && (
        <section className="space-y-4 text-sm">
          {agent.paused_reason && (
            <PausedBanner
              reason={agent.paused_reason}
              disabled={readOnly || resuming}
              onResume={handleResume}
              resuming={resuming}
            />
          )}
          {readOnly && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              {t('aiLibrary.agents.presetReadOnly')}
            </div>
          )}

          <div className="flex items-center gap-3">
            <AgentIconPicker
              value={draft.icon ?? null}
              onChange={(slug) => updateDraft('icon', slug)}
              disabled={readOnly}
              size={24}
            />
            <p className="text-xs text-zinc-500">
              {t(
                'aiLibrary.agents.iconHint',
                'Icon shown in the sidebar and throughout the app.',
              )}
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400">
                {t('aiLibrary.agents.nameLabel', 'Name')}
              </label>
              <input
                type="text"
                value={draft.name ?? ''}
                onChange={(e) => updateDraft('name', e.target.value)}
                disabled={readOnly}
                className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400">
                {t('aiLibrary.agents.slugLabel', 'Slug')}
              </label>
              <input
                type="text"
                value={agent.slug}
                disabled
                className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-400 font-mono cursor-not-allowed"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400">
              {t('aiLibrary.agents.descriptionLabel', 'Description')}
            </label>
            <textarea
              value={draft.description ?? ''}
              onChange={(e) => updateDraft('description', e.target.value)}
              disabled={readOnly}
              rows={2}
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400">
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
            })}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400">
                {t('aiLibrary.agents.temperatureLabel', 'Temperature')}
              </label>
              <input
                type="number"
                step="0.1"
                min={0}
                max={2}
                value={draft.temperature ?? 0}
                onChange={(e) => updateDraft('temperature', Number(e.target.value))}
                disabled={readOnly}
                className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400">
                {t('aiLibrary.agents.maxTokensLabel', 'Max tokens')}
              </label>
              <input
                type="number"
                min={1}
                max={100000}
                value={draft.max_tokens ?? 0}
                onChange={(e) => updateDraft('max_tokens', Number(e.target.value))}
                disabled={readOnly}
                className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400">
                {t('aiLibrary.agents.enabledLabel', 'Enabled')}
              </label>
              <label className="mt-1 flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100">
                <input
                  type="checkbox"
                  checked={draft.enabled ?? false}
                  onChange={(e) => updateDraft('enabled', e.target.checked)}
                  disabled={readOnly}
                  className="h-4 w-4"
                />
                <span>{(draft.enabled ?? false) ? t('common.yes', 'Yes') : t('common.no', 'No')}</span>
              </label>
            </div>
          </div>

          <BudgetFields
            tokenBudget={draft.monthly_token_budget ?? null}
            costCentsBudget={draft.monthly_cost_cents_budget ?? null}
            disabled={readOnly}
            onTokenChange={(v) => updateDraft('monthly_token_budget', v)}
            onCostChange={(v) => updateDraft('monthly_cost_cents_budget', v)}
          />

          <div className="pt-1 text-xs text-zinc-500">
            {t('aiLibrary.agents.boundSkills')}: <span className="text-zinc-200 font-medium">{agent.skill_ids.length}</span>
          </div>
        </section>
      )}

      {sub === 'files' && (
        <section className="space-y-5">
          {readOnly && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              {t('aiLibrary.agents.presetReadOnly')}
            </div>
          )}
          <div>
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">
              {t('aiLibrary.agents.identityTitle')} <span className="text-zinc-500 font-normal">(IDENTITY.md)</span>
            </h3>
            <MarkdownEditor
              value={draft.identity_md ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, identity_md: v }))}
              disabled={readOnly}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">
              {t('aiLibrary.agents.soulTitle')} <span className="text-zinc-500 font-normal">(SOUL.md)</span>
            </h3>
            <MarkdownEditor
              value={draft.soul_md ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, soul_md: v }))}
              disabled={readOnly}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">
              {t('aiLibrary.agents.instructionsTitle')} <span className="text-zinc-500 font-normal">(AGENT.md)</span>
            </h3>
            <MarkdownEditor
              value={draft.agent_md ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, agent_md: v }))}
              disabled={readOnly}
              rows={16}
            />
          </div>
        </section>
      )}

      {sub === 'skills' && (
        <SkillsSection
          localSkillIds={localSkillIds}
          allSkills={allSkills}
          skillsLoading={skillsLoading}
          readOnly={readOnly}
          onAdd={addSkill}
          onRemove={removeSkill}
          onMove={moveSkill}
        />
      )}

      {sub === 'runs' && <RunsSection slug={slug} />}

      {forkModalOpen && (
        <NewAgentModal
          existingAgents={allAgents.length > 0 ? allAgents : [agent]}
          initialForkFrom={agent.slug}
          onClose={() => setForkModalOpen(false)}
          onCreated={handleForkCreated}
        />
      )}
    </div>
  );
};

/**
 * Tiny presentational badge showing the agent's scope: system preset,
 * team-scoped, project-scoped, or private (falls back to showing nothing
 * for now when ``is_system_preset=false`` and both team/project are null —
 * keeps the header uncluttered for the common per-user case).
 */
const ScopeBadge: React.FC<{ agent: AILibraryAgent }> = ({ agent }) => {
  const { t } = useTranslation();
  const base =
    'ml-1 rounded border px-2 py-0.5 whitespace-nowrap';

  if (agent.is_system_preset) {
    return (
      <span className={`${base} border-zinc-700 bg-zinc-800 text-zinc-300`}>
        {t('aiLibrary.agents.systemPreset', 'System Preset')}
      </span>
    );
  }
  if (agent.team_id != null) {
    return (
      <span className={`${base} border-indigo-500/40 bg-indigo-500/10 text-indigo-300`}>
        {t('aiLibrary.agents.scopeBadgeTeam', 'Team: {{name}}', {
          name: agent.team_name ?? agent.team_id,
        })}
      </span>
    );
  }
  if (agent.project_id != null) {
    return (
      <span className={`${base} border-emerald-500/40 bg-emerald-500/10 text-emerald-300`}>
        {t('aiLibrary.agents.scopeBadgeProject', 'Project: {{name}}', {
          name: agent.project_name ?? agent.project_id,
        })}
      </span>
    );
  }
  return (
    <span className={`${base} border-zinc-700 bg-zinc-900 text-zinc-400`}>
      {t('aiLibrary.agents.scopeBadgePrivate', 'Private')}
    </span>
  );
};

/**
 * Model group — one entry per enabled provider, with its available models.
 * Used to render grouped <optgroup> in the model picker.
 */
interface ProviderModelGroup {
  providerKey: string;
  providerName: string;
  models: string[];
}

// Friendly names for providers when the Overview model picker renders optgroups.
// Keep in sync with AISettings.tsx PROVIDER_META (we don't import from there to
// avoid a circular-ish dependency; this mapping is small and stable).
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  doubao: 'Doubao',
  minimax: 'MiniMax',
  kimi: 'Kimi',
  qwen: 'Qwen',
  volcengine: 'Volcengine',
  ollama: 'Ollama',
  lmstudio: 'LM Studio',
};

/**
 * Collect the curated whitelist of models exposed by every enabled
 * provider in aiSettings. Reads ``config.enabled_models`` (set in the
 * AI Settings UI via "Add Model" chips) — this is intentionally narrow
 * so users see only the models they care about, not the full 100+
 * provider catalog.
 *
 * Falls back to ``[selected_model]`` when ``enabled_models`` is missing
 * (legacy accounts; aiService.getAISettings auto-seeds this on read).
 */
export function getAvailableModels(
  settings: AISettingsType | null | undefined,
): ProviderModelGroup[] {
  if (!settings?.providers) return [];
  return Object.entries(settings.providers)
    .filter(([, config]) => config?.enabled)
    .map(([key, config]) => {
      const whitelist = config?.enabled_models;
      const fallback = config?.selected_model ? [config.selected_model] : [];
      const models = Array.from(new Set(whitelist ?? fallback)).filter(Boolean);
      return {
        providerKey: key,
        providerName: PROVIDER_DISPLAY_NAMES[key] ?? key,
        models,
      };
    })
    .filter((g) => g.models.length > 0);
}

/**
 * Render the grouped model <select>. If the current value is not present in
 * any provider group (e.g. the user has disabled the provider that owned it),
 * we still show it as a leading disabled option so the user sees the stale
 * selection rather than it silently flipping to the first option.
 */
function renderModelSelect(params: {
  value: string;
  groups: ProviderModelGroup[];
  disabled: boolean;
  onChange: (v: string) => void;
  providerNotEnabledLabel: string;
}): React.ReactElement {
  const { value, groups, disabled, onChange, providerNotEnabledLabel } = params;
  const knownModels = new Set(groups.flatMap((g) => g.models));
  const showOrphan = value !== '' && !knownModels.has(value);

  return (
    <div className="relative mt-1">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full appearance-none rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 pr-8 text-sm font-mono text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {showOrphan && (
          <option value={value}>
            {value} ({providerNotEnabledLabel})
          </option>
        )}
        {groups.length === 0 && !showOrphan && (
          <option value="">No models available — enable a provider</option>
        )}
        {groups.map((group) => (
          <optgroup key={group.providerKey} label={group.providerName}>
            {group.models.map((m) => (
              <option key={`${group.providerKey}:${m}`} value={m}>
                {m}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
      />
    </div>
  );
}

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
    return <p className="text-sm text-zinc-500">Loading skills...</p>;
  }

  return (
    <section className="space-y-6">
      {readOnly && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {t('aiLibrary.agents.presetReadOnly')}
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold text-zinc-200">
          {t('aiLibrary.agents.currentSkills', 'Bound Skills')}
          <span className="ml-2 text-xs font-normal text-zinc-500">
            ({boundSkills.length})
          </span>
        </h3>
        {boundSkills.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 px-3 py-4 text-sm text-zinc-500">
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
                className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2"
              >
                <span className="text-xl leading-none" aria-hidden>
                  {skill?.icon ?? ''}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-zinc-100">
                    {skill?.name ?? `Skill #${id}`}
                  </div>
                  {skill?.slug && (
                    <div className="truncate font-mono text-xs text-zinc-500">
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
                      className="rounded-md border border-zinc-700 bg-zinc-800 p-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
                      title={t('aiLibrary.agents.moveUp', 'Move up')}
                      aria-label={t('aiLibrary.agents.moveUp', 'Move up')}
                    >
                      <ArrowUp size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onMove(id, 1)}
                      disabled={idx === boundSkills.length - 1}
                      className="rounded-md border border-zinc-700 bg-zinc-800 p-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
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
          <h3 className="mb-2 text-sm font-semibold text-zinc-200">
            {t('aiLibrary.agents.availableSkills', 'Available Skills')}
            <span className="ml-2 text-xs font-normal text-zinc-500">
              ({availableSkills.length})
            </span>
          </h3>
          {availableSkills.length === 0 ? (
            <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 px-3 py-4 text-sm text-zinc-500">
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
                  className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2"
                >
                  <span className="text-xl leading-none" aria-hidden>
                    {skill.icon ?? ''}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-zinc-100">
                      {skill.name}
                    </div>
                    {skill.slug && (
                      <div className="truncate font-mono text-xs text-zinc-500">
                        {skill.slug}
                      </div>
                    )}
                  </div>
                  <SkillScopeBadge skill={skill} />
                  <button
                    type="button"
                    onClick={() => onAdd(skill.id)}
                    className="inline-flex items-center gap-1 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20"
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
      <span className={`${base} border-zinc-700 bg-zinc-800 text-zinc-300`}>
        {t('aiLibrary.agents.systemPreset', 'System Preset')}
      </span>
    );
  }
  if (skill.team_id != null) {
    return (
      <span className={`${base} border-indigo-500/40 bg-indigo-500/10 text-indigo-300`}>
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
    <span className={`${base} border-zinc-700 bg-zinc-900 text-zinc-400`}>
      {t('aiLibrary.skills.scopeBadgePrivate', 'Private')}
    </span>
  );
};

/**
 * Build the editable draft subset from a hydrated agent. Keeps keys that the
 * Overview + Files forms edit; omits server-managed fields (id, timestamps,
 * is_system_preset, skill_ids, ownership FKs).
 */
function buildDraft(a: AILibraryAgent): Partial<AILibraryAgent> {
  return {
    name: a.name,
    description: a.description ?? '',
    icon: a.icon ?? null,
    model: a.model,
    temperature: a.temperature,
    max_tokens: a.max_tokens,
    enabled: a.enabled,
    identity_md: a.identity_md ?? '',
    soul_md: a.soul_md ?? '',
    agent_md: a.agent_md ?? '',
    monthly_token_budget: a.monthly_token_budget ?? null,
    monthly_cost_cents_budget: a.monthly_cost_cents_budget ?? null,
  };
}

/**
 * Runs sub-tab body — lists the authenticated caller's historical invocations
 * of this agent, newest first. Polls every 10 s so running rows advance
 * without a page reload. Click a row → open {@link RunDetailModal} with
 * metadata, input/output summaries, and snapshot prices.
 */
const RunsSection: React.FC<{ slug: string }> = ({ slug }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [page, setPage] = useState<AgentRunListResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // Ref so the poll callback always sees the latest offset without re-creating
  // the interval every time it changes.
  const offsetRef = useRef(offset);
  offsetRef.current = offset;

  const fetchPage = useCallback(
    async (targetOffset: number, mode: 'initial' | 'poll') => {
      if (mode === 'initial') setLoading(true);
      else setRefreshing(true);
      try {
        const resp = await aiLibraryService.listAgentRuns(
          slug,
          RUNS_PAGE_SIZE,
          targetOffset,
        );
        setPage(resp);
        setError(null);
      } catch (err) {
        console.error('[RunsSection] listAgentRuns failed:', err);
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
        // Only surface a toast on explicit user-triggered fetches — polling
        // errors stay silent so a brief network blip doesn't spam the UI.
        if (mode === 'initial') {
          addToast(`Failed to load runs: ${msg}`, 'error');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [slug, addToast],
  );

  // Initial load + reload when slug or offset changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await fetchPage(offset, 'initial');
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchPage, offset]);

  // Poll every 10 s to pick up heartbeat updates / newly completed runs.
  // We don't use Realtime here: the runs list is bounded (25 rows) and the
  // server-side total changes cheaply, and polling avoids a second Realtime
  // subscription per opened agent editor.
  useEffect(() => {
    const id = window.setInterval(() => {
      void fetchPage(offsetRef.current, 'poll');
    }, RUNS_POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [fetchPage]);

  const total = page?.total ?? 0;
  const items = page?.items ?? [];
  const hasPrev = offset > 0;
  const hasNext = offset + RUNS_PAGE_SIZE < total;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + RUNS_PAGE_SIZE, total);

  const handleRefresh = (): void => {
    void fetchPage(offset, 'initial');
  };

  const handleCancel = async (runId: string): Promise<void> => {
    try {
      await aiLibraryService.cancelRun(runId);
      addToast(
        t('aiLibrary.agents.runs.cancelRequested', 'Cancel requested'),
        'success',
      );
      await fetchPage(offset, 'poll');
    } catch (err) {
      console.error('[RunsSection] cancelRun failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      addToast(`Failed to cancel run: ${msg}`, 'error');
    }
  };

  if (loading && page === null) {
    return (
      <p className="text-sm text-zinc-500">
        {t('aiLibrary.agents.runs.loading', 'Loading runs...')}
      </p>
    );
  }

  if (error && page === null) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        {t('aiLibrary.agents.runs.loadError', 'Failed to load runs')}: {error}
      </div>
    );
  }

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between gap-3">
        <div className="text-xs text-zinc-500">
          {total === 0
            ? t('aiLibrary.agents.runs.emptyHeader', 'No runs yet')
            : t('aiLibrary.agents.runs.paginationLabel', {
                defaultValue: 'Showing {{start}}–{{end}} of {{total}}',
                start: pageStart,
                end: pageEnd,
                total,
              })}
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading || refreshing}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
          title={t('aiLibrary.agents.runs.refresh', 'Refresh')}
        >
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          {t('aiLibrary.agents.runs.refresh', 'Refresh')}
        </button>
      </header>

      {items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 px-3 py-8 text-center text-sm text-zinc-500">
          {t(
            'aiLibrary.agents.runs.emptyBody',
            'This agent has not been invoked yet. Start a chat or task to see activity here.',
          )}
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900/60 text-xs font-medium uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-3 py-2">
                  {t('aiLibrary.agents.runs.colStarted', 'Started')}
                </th>
                <th className="px-3 py-2">
                  {t('aiLibrary.agents.runs.colStatus', 'Status')}
                </th>
                <th className="px-3 py-2">
                  {t('aiLibrary.agents.runs.colTrigger', 'Trigger')}
                </th>
                <th className="px-3 py-2 text-right">
                  {t('aiLibrary.agents.runs.colTokens', 'Tokens')}
                </th>
                <th className="px-3 py-2 text-right">
                  {t('aiLibrary.agents.runs.colCost', 'Cost')}
                </th>
                <th className="px-3 py-2 text-right">
                  {t('aiLibrary.agents.runs.colDuration', 'Duration')}
                </th>
                <th className="px-3 py-2 w-8" aria-hidden />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {items.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  onOpen={() => setSelectedRunId(run.id)}
                  onCancel={() => handleCancel(run.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(hasPrev || hasNext) && (
        <footer className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => setOffset(Math.max(0, offset - RUNS_PAGE_SIZE))}
            disabled={!hasPrev || loading}
            className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft size={14} />
            {t('common.previous', 'Previous')}
          </button>
          <button
            type="button"
            onClick={() => setOffset(offset + RUNS_PAGE_SIZE)}
            disabled={!hasNext || loading}
            className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t('common.next', 'Next')}
            <ChevronRight size={14} />
          </button>
        </footer>
      )}

      {selectedRunId && (
        <RunDetailModal
          runId={selectedRunId}
          onClose={() => setSelectedRunId(null)}
          onCancel={() => handleCancel(selectedRunId)}
        />
      )}
    </section>
  );
};

const RunRow: React.FC<{
  run: AgentRunListItem;
  onOpen: () => void;
  onCancel: () => void;
}> = ({ run, onOpen, onCancel }) => {
  const { t } = useTranslation();
  const isRunning = run.status === 'running';
  return (
    <tr
      className="cursor-pointer transition-colors hover:bg-zinc-900/60"
      onClick={onOpen}
    >
      <td className="px-3 py-2 whitespace-nowrap text-zinc-300">
        {formatTimestamp(run.started_at)}
      </td>
      <td className="px-3 py-2">
        <RunStatusBadge status={run.status} />
      </td>
      <td className="px-3 py-2 font-mono text-xs text-zinc-400">
        {run.trigger}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
        {formatTokens(run.total_tokens)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
        {formatCost(run.cost_cents)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
        {formatDuration(run.started_at, run.ended_at)}
      </td>
      <td className="px-3 py-2 text-right">
        {isRunning && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCancel();
            }}
            className="rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs font-medium text-red-300 hover:bg-red-500/20"
            title={t('aiLibrary.agents.runs.cancel', 'Cancel')}
          >
            {t('aiLibrary.agents.runs.cancel', 'Cancel')}
          </button>
        )}
      </td>
    </tr>
  );
};

const RunStatusBadge: React.FC<{ status: AgentRunStatus }> = ({ status }) => {
  const { t } = useTranslation();
  const styles: Record<AgentRunStatus, string> = {
    running:
      'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    completed:
      'border-zinc-700 bg-zinc-800 text-zinc-200',
    failed: 'border-red-500/40 bg-red-500/10 text-red-300',
    cancelled:
      'border-amber-500/40 bg-amber-500/10 text-amber-300',
    heartbeat_lost:
      'border-orange-500/40 bg-orange-500/10 text-orange-300',
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${styles[status]}`}
    >
      {status === 'running' && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
        </span>
      )}
      {t(`aiLibrary.agents.runs.status.${status}`, status)}
    </span>
  );
};

/**
 * Full-detail modal for a single run. Fetches {@link AgentRunDetail} on open,
 * renders summaries + metadata + snapshot price. Provides a Cancel button when
 * the run is still live.
 */
const RunDetailModal: React.FC<{
  runId: string;
  onClose: () => void;
  onCancel: () => void;
}> = ({ runId, onClose, onCancel }) => {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    aiLibraryService
      .getRun(runId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[RunDetailModal] getRun failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900 shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800 px-5 py-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-zinc-100">
              {t('aiLibrary.agents.runs.detailTitle', 'Run detail')}
            </h3>
            <div className="mt-0.5 truncate font-mono text-xs text-zinc-500">
              {runId}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {detail?.status === 'running' && !detail.cancel_requested && (
              <button
                type="button"
                onClick={onCancel}
                className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20"
              >
                {t('aiLibrary.agents.runs.cancel', 'Cancel')}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-zinc-700 bg-zinc-800 p-1.5 text-zinc-300 hover:bg-zinc-700"
              aria-label={t('common.close', 'Close')}
            >
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
              {error}
            </div>
          )}
          {!detail && !error && (
            <p className="text-sm text-zinc-500">
              {t('aiLibrary.agents.runs.loading', 'Loading runs...')}
            </p>
          )}
          {detail && <RunDetailBody detail={detail} />}
        </div>
      </div>
    </div>
  );
};

const RunDetailBody: React.FC<{ detail: AgentRunDetail }> = ({ detail }) => {
  const { t } = useTranslation();

  const fields: Array<[string, React.ReactNode]> = [
    [t('aiLibrary.agents.runs.colStatus', 'Status'), <RunStatusBadge key="s" status={detail.status} />],
    [t('aiLibrary.agents.runs.colTrigger', 'Trigger'), <span key="t" className="font-mono text-xs">{detail.trigger}</span>],
    [t('aiLibrary.agents.runs.fieldModel', 'Model'), detail.model ?? '—'],
    [t('aiLibrary.agents.runs.fieldProvider', 'Provider'), detail.provider ?? '—'],
    [t('aiLibrary.agents.runs.colStarted', 'Started'), formatTimestamp(detail.started_at)],
    [
      t('aiLibrary.agents.runs.fieldEnded', 'Ended'),
      detail.ended_at ? formatTimestamp(detail.ended_at) : '—',
    ],
    [t('aiLibrary.agents.runs.fieldHeartbeat', 'Heartbeat'), formatTimestamp(detail.heartbeat_at)],
    [t('aiLibrary.agents.runs.colDuration', 'Duration'), formatDuration(detail.started_at, detail.ended_at)],
    [
      t('aiLibrary.agents.runs.fieldTokens', 'Tokens (in / out / total)'),
      `${formatTokens(detail.prompt_tokens)} / ${formatTokens(detail.completion_tokens)} / ${formatTokens(detail.total_tokens)}`,
    ],
    [t('aiLibrary.agents.runs.colCost', 'Cost'), formatCost(detail.cost_cents)],
    [
      t('aiLibrary.agents.runs.fieldSnapshotPrompt', 'Prompt price snapshot'),
      formatPriceSnapshot(detail.prompt_cents_per_1k_snapshot),
    ],
    [
      t('aiLibrary.agents.runs.fieldSnapshotCompletion', 'Completion price snapshot'),
      formatPriceSnapshot(detail.completion_cents_per_1k_snapshot),
    ],
    [
      t('aiLibrary.agents.runs.fieldSkills', 'Skills used'),
      detail.skill_slugs_used.length === 0
        ? '—'
        : detail.skill_slugs_used.join(', '),
    ],
  ];

  return (
    <div className="space-y-5 text-sm">
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 border-b border-zinc-800/60 py-1">
            <dt className="text-xs font-medium text-zinc-500">{label}</dt>
            <dd className="text-right text-zinc-200 truncate">{value}</dd>
          </div>
        ))}
      </dl>

      {detail.cancel_requested && detail.status === 'running' && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-300">
          {t(
            'aiLibrary.agents.runs.cancelPendingNote',
            'Cancel has been requested. The runner will observe it between tool iterations.',
          )}
        </div>
      )}

      {detail.input_summary && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {t('aiLibrary.agents.runs.fieldInputSummary', 'Input summary')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-200">
            {detail.input_summary}
          </pre>
        </section>
      )}

      {detail.output_summary && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {t('aiLibrary.agents.runs.fieldOutputSummary', 'Output summary')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-200">
            {detail.output_summary}
          </pre>
        </section>
      )}

      {detail.error_message && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-red-400">
            {t('aiLibrary.agents.runs.fieldError', 'Error')}
            {detail.error_code ? ` (${detail.error_code})` : ''}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-200">
            {detail.error_message}
          </pre>
        </section>
      )}

      {Object.keys(detail.metadata_json).length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {t('aiLibrary.agents.runs.fieldMetadata', 'Metadata')}
          </h4>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs font-mono text-zinc-300">
            {JSON.stringify(detail.metadata_json, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
};

// ─── Formatting helpers ────────────────────────────────────────────────────

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Locale-aware, short date + time. Matches the compact table layout.
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '0';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function formatCost(centsFractional: number | null | undefined): string {
  if (centsFractional == null) return '—';
  // Cost is stored as fractional cents. A $0.0045 run shows as 0.45¢.
  const cents = Number(centsFractional);
  if (!Number.isFinite(cents)) return '—';
  if (cents < 1) return `${cents.toFixed(3)}¢`;
  if (cents < 100) return `${cents.toFixed(2)}¢`;
  return `$${(cents / 100).toFixed(2)}`;
}

function formatPriceSnapshot(
  centsPer1k: number | null | undefined,
): string {
  if (centsPer1k == null) return '—';
  const v = Number(centsPer1k);
  if (!Number.isFinite(v)) return '—';
  return `${v.toFixed(4)}¢ / 1k`;
}

function formatDuration(startIso: string, endIso: string | null | undefined): string {
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—';
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/**
 * Top-of-Overview banner shown when the agent has a `paused_reason` set.
 *
 * - `'budget'` (amber): the sweeper found spend over the cap. Resume clears
 *   the flag, but if the budget isn't raised first, the sweeper will
 *   re-pause within ~60 s — the copy says so.
 * - `'manual'` (zinc): an admin / owner hit Pause. Resume re-enables.
 *
 * The Resume button is also disabled for preset agents (readOnly) since
 * Phase 1 policy blocks writes on system presets.
 */
const PausedBanner: React.FC<{
  reason: 'budget' | 'manual';
  disabled: boolean;
  resuming: boolean;
  onResume: () => void;
}> = ({ reason, disabled, resuming, onResume }) => {
  const { t } = useTranslation();
  const isBudget = reason === 'budget';
  const wrap = isBudget
    ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
    : 'border-zinc-700 bg-zinc-800 text-zinc-200';
  const icon = isBudget ? 'text-amber-400' : 'text-zinc-400';
  const title = isBudget
    ? t('aiLibrary.agents.budget.pausedTitleBudget', 'Paused — monthly budget exceeded')
    : t('aiLibrary.agents.budget.pausedTitleManual', 'Paused manually');
  const body = isBudget
    ? t(
        'aiLibrary.agents.budget.pausedBodyBudget',
        'The sweeper detected this agent ran over its token or cost budget this month. Raise the budget below before resuming — otherwise the sweeper will re-pause within a minute.',
      )
    : t(
        'aiLibrary.agents.budget.pausedBodyManual',
        'An owner or admin paused this agent. Click Resume to re-enable.',
      );
  return (
    <div className={`flex items-start gap-3 rounded-lg border px-3 py-3 text-xs ${wrap}`}>
      <AlertTriangle size={16} className={`flex-shrink-0 mt-0.5 ${icon}`} />
      <div className="flex-1 min-w-0">
        <div className="font-medium">{title}</div>
        <p className="mt-0.5 opacity-90">{body}</p>
      </div>
      <button
        type="button"
        onClick={onResume}
        disabled={disabled}
        className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40 whitespace-nowrap"
      >
        <Play size={12} />
        {resuming
          ? t('aiLibrary.agents.budget.resuming', 'Resuming...')
          : t('aiLibrary.agents.budget.resume', 'Resume')}
      </button>
    </div>
  );
};

/**
 * Budget input row. Two numeric fields: monthly token budget and monthly
 * cost budget (in cents). Empty / 0 means unlimited (the backend normalizes
 * 0 → NULL before persisting, so the sweeper's `is not None` cap check
 * treats both the same way).
 *
 * Uses string-valued inputs internally so the user can cleanly delete all
 * digits without React emitting spurious 0s or NaN — we parse on change and
 * send `null` back up when the field is empty.
 */
const BudgetFields: React.FC<{
  tokenBudget: number | null;
  costCentsBudget: number | null;
  disabled: boolean;
  onTokenChange: (value: number | null) => void;
  onCostChange: (value: number | null) => void;
}> = ({ tokenBudget, costCentsBudget, disabled, onTokenChange, onCostChange }) => {
  const { t } = useTranslation();
  // Display dollars for the cost budget to match the Runs tab's cost column,
  // but we still PATCH the column in cents. 500 cents ⇢ "5.00" displayed.
  const dollarsStr = costCentsBudget != null ? (costCentsBudget / 100).toFixed(2) : '';
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        {t('aiLibrary.agents.budget.sectionLabel', 'Monthly budget')}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-zinc-400">
            {t('aiLibrary.agents.budget.tokenBudgetLabel', 'Token budget')}
          </label>
          <input
            type="number"
            min={0}
            step={1000}
            value={tokenBudget ?? ''}
            placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === '') {
                onTokenChange(null);
                return;
              }
              const parsed = Number(raw);
              onTokenChange(Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : null);
            }}
            disabled={disabled}
            className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
          />
          <p className="mt-1 text-xs text-zinc-500">
            {t(
              'aiLibrary.agents.budget.tokenBudgetHint',
              'Cap on total prompt+completion tokens this calendar month. Blank or 0 = unlimited.',
            )}
          </p>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-400">
            {t('aiLibrary.agents.budget.costBudgetLabel', 'Cost budget (USD)')}
          </label>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-zinc-500 text-sm">$</span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={dollarsStr}
              placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === '') {
                  onCostChange(null);
                  return;
                }
                const dollars = Number(raw);
                if (!Number.isFinite(dollars)) {
                  onCostChange(null);
                  return;
                }
                // cents = round(dollars * 100) — avoid float drift like 19.99 * 100 === 1998.9999...
                onCostChange(Math.max(0, Math.round(dollars * 100)));
              }}
              disabled={disabled}
              className="flex-1 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
            />
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            {t(
              'aiLibrary.agents.budget.costBudgetHint',
              'Hard cap on this month\'s spend. The sweeper pauses the agent within ~60s of crossing the cap.',
            )}
          </p>
        </div>
      </div>
    </div>
  );
};

export default AgentEditor;
