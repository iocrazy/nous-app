// frontend/components/AILibrary/AgentEditor.tsx
// Agent edit pane — Overview / Files / Skills sub-tabs.
//
// - System-preset agents are read-only (save button hidden, textareas disabled).
// - Draft state is local; `save()` PATCHes via aiLibraryService and replaces
//   the hydrated agent immutably on success.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { MarkdownEditor } from './MarkdownEditor';

type SubTab = 'overview' | 'files' | 'skills';

interface AgentEditorProps {
  slug: string;
}

export const AgentEditor: React.FC<AgentEditorProps> = ({ slug }) => {
  const { t } = useTranslation();
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
        setDraft({
          identity_md: a.identity_md ?? '',
          soul_md: a.soul_md ?? '',
          agent_md: a.agent_md ?? '',
        });
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

  const save = async (): Promise<void> => {
    if (isPreset) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await aiLibraryService.updateAgent(slug, draft);
      setAgent(updated);
      setDraft({
        identity_md: updated.identity_md ?? '',
        soul_md: updated.soul_md ?? '',
        agent_md: updated.agent_md ?? '',
      });
    } catch (err) {
      console.error('[AgentEditor] updateAgent failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
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
              <span className="ml-1 rounded bg-zinc-800 px-2 py-0.5 text-zinc-300">
                {t('aiLibrary.agents.systemPreset')}
              </span>
            )}
          </div>
        </div>
        {!isPreset && (
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
        <section className="space-y-3 text-sm">
          <dl className="grid grid-cols-[140px_1fr] gap-y-2 gap-x-4">
            <dt className="text-zinc-500">Description</dt>
            <dd className="text-zinc-200">{agent.description ?? '—'}</dd>

            <dt className="text-zinc-500">Model</dt>
            <dd className="text-zinc-200 font-mono text-xs">{agent.model}</dd>

            <dt className="text-zinc-500">Temperature</dt>
            <dd className="text-zinc-200">{agent.temperature}</dd>

            <dt className="text-zinc-500">Max tokens</dt>
            <dd className="text-zinc-200">{agent.max_tokens}</dd>

            <dt className="text-zinc-500">Enabled</dt>
            <dd className="text-zinc-200">{agent.enabled ? 'Yes' : 'No'}</dd>

            <dt className="text-zinc-500">Bound skills</dt>
            <dd className="text-zinc-200">{agent.skill_ids.length}</dd>
          </dl>
        </section>
      )}

      {sub === 'files' && (
        <section className="space-y-5">
          {isPreset && (
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
              disabled={isPreset}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">
              {t('aiLibrary.agents.soulTitle')} <span className="text-zinc-500 font-normal">(SOUL.md)</span>
            </h3>
            <MarkdownEditor
              value={draft.soul_md ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, soul_md: v }))}
              disabled={isPreset}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold text-zinc-200">
              {t('aiLibrary.agents.instructionsTitle')} <span className="text-zinc-500 font-normal">(AGENT.md)</span>
            </h3>
            <MarkdownEditor
              value={draft.agent_md ?? ''}
              onChange={(v) => setDraft((d) => ({ ...d, agent_md: v }))}
              disabled={isPreset}
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

export default AgentEditor;
