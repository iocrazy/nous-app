// frontend/components/AILibrary/AgentEditor.tsx
// Agent edit pane — Overview / Files / Skills sub-tabs.
//
// - Overview: editable form (name / description / model / temperature /
//   max_tokens / enabled). Disabled for system presets unless the user is admin.
// - Files: IDENTITY.md / SOUL.md / AGENT.md editors (preset = read-only).
// - Draft state is local; `save()` PATCHes via aiLibraryService and replaces
//   the hydrated agent immutably on success.

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent, AISettings as AISettingsType } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { ChevronDown, GitFork } from 'lucide-react';
import { MarkdownEditor } from './MarkdownEditor';
import { NewAgentModal } from './NewAgentModal';

type SubTab = 'overview' | 'files' | 'skills';

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
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [allAgents, setAllAgents] = useState<AILibraryAgent[]>([]);

  useEffect(() => {
    let cancelled = false;
    setAgent(null);
    setError(null);
    setSub('overview');

    aiLibraryService
      .getAgent(slug)
      .then((a) => {
        if (cancelled) return;
        setAgent(a);
        setDraft(buildDraft(a));
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

  const save = async (): Promise<void> => {
    if (readOnly) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await aiLibraryService.updateAgent(slug, draft);
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(t('aiLibrary.agents.saved', 'Agent saved'), 'success');
    } catch (err) {
      console.error('[AgentEditor] updateAgent failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(`Failed to save agent: ${msg}`, 'error');
    } finally {
      setSaving(false);
    }
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

  const subTabs: SubTab[] = ['overview', 'files', 'skills'];

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
          {readOnly && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              {t('aiLibrary.agents.presetReadOnly')}
            </div>
          )}

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
        <section>
          <p className="text-sm text-zinc-400">
            {t('aiLibrary.agents.boundSkills')}: <span className="text-zinc-200 font-medium">{agent.skill_ids.length}</span>
          </p>
          {/* Phase 2: multi-select with toggles to bind/unbind skills */}
        </section>
      )}

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
 * Collect the union of models exposed by every enabled provider in aiSettings.
 * Prefers `config.models` detected from the server (via "Test Connection"); if
 * the provider has no detected models yet, returns an empty list for that
 * provider (the dropdown hides empty groups).
 *
 * Exported as a named function (not inside the component) so it can be unit-
 * tested later without a React render.
 */
export function getAvailableModels(
  settings: AISettingsType | null | undefined,
): ProviderModelGroup[] {
  if (!settings?.providers) return [];
  return Object.entries(settings.providers)
    .filter(([, config]) => config?.enabled)
    .map(([key, config]) => ({
      providerKey: key,
      providerName: PROVIDER_DISPLAY_NAMES[key] ?? key,
      models: Array.from(new Set(config?.models ?? [])).filter(Boolean),
    }))
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
 * Build the editable draft subset from a hydrated agent. Keeps keys that the
 * Overview + Files forms edit; omits server-managed fields (id, timestamps,
 * is_system_preset, skill_ids, ownership FKs).
 */
function buildDraft(a: AILibraryAgent): Partial<AILibraryAgent> {
  return {
    name: a.name,
    description: a.description ?? '',
    model: a.model,
    temperature: a.temperature,
    max_tokens: a.max_tokens,
    enabled: a.enabled,
    identity_md: a.identity_md ?? '',
    soul_md: a.soul_md ?? '',
    agent_md: a.agent_md ?? '',
  };
}

export default AgentEditor;
