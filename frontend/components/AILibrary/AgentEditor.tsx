// frontend/components/AILibrary/AgentEditor.tsx
// Agent edit pane — Overview / Files / Skills sub-tabs.
//
// - Overview: editable form (name / description / model / temperature /
//   max_tokens / enabled). Disabled for system presets unless the user is admin.
// - Files: IDENTITY.md / SOUL.md / AGENT.md editors (preset = read-only).
// - Draft state is local; `save()` PATCHes via aiLibraryService and replaces
//   the hydrated agent immutably on success.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { MarkdownEditor } from './MarkdownEditor';

type SubTab = 'overview' | 'files' | 'skills';

interface AgentEditorProps {
  slug: string;
  onSelectAgent?: (slug: string) => void;
}

export const AgentEditor: React.FC<AgentEditorProps> = ({ slug, onSelectAgent: _onSelectAgent }) => {
  const { t } = useTranslation();
  const { userProfile } = useAuth();
  const { addToast } = useToast();
  const isAdmin = userProfile.role === 'admin';
  const [agent, setAgent] = useState<AILibraryAgent | null>(null);
  const [sub, setSub] = useState<SubTab>('overview');
  const [draft, setDraft] = useState<Partial<AILibraryAgent>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
            {isPreset && (
              <span className="ml-1 rounded border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-zinc-300">
                {t('aiLibrary.agents.systemPreset')}
              </span>
            )}
          </div>
        </div>
        {!readOnly && (
          <button
            onClick={save}
            disabled={saving}
            className="rounded-lg bg-indigo-500/10 border border-indigo-500/30 px-4 py-2 text-sm font-medium text-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
          >
            {saving ? 'Saving...' : t('aiLibrary.agents.saveChanges')}
          </button>
        )}
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
            <input
              type="text"
              value={draft.model ?? ''}
              onChange={(e) => updateDraft('model', e.target.value)}
              disabled={readOnly}
              className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-mono text-zinc-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
            />
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
    </div>
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
