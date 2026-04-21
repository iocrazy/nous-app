// frontend/components/AILibrary/AgentsTab.tsx
// Left rail: list of agents (system presets + custom).
// Right pane: AgentEditor for the selected agent.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentEditor } from './AgentEditor';

export const AgentsTab: React.FC = () => {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    aiLibraryService
      .listAgents()
      .then((list) => {
        if (cancelled) return;
        setAgents(list);
        if (list.length > 0) setSelectedSlug(list[0].slug);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentsTab] listAgents failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div className="p-6 text-sm text-zinc-500">Loading agents...</div>;
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load agents: {error}
        </div>
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <div className="p-6 text-sm text-zinc-500">
        {t('aiLibrary.agents.selectAgent')}
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <aside className="w-64 flex-shrink-0 overflow-y-auto border-r border-zinc-800 bg-zinc-950/40">
        {agents.map((a) => {
          const active = selectedSlug === a.slug;
          return (
            <button
              key={a.slug}
              onClick={() => setSelectedSlug(a.slug)}
              className={`flex w-full flex-col items-start border-b border-zinc-800/60 px-4 py-3 text-left transition-colors ${
                active ? 'bg-indigo-500/10 text-zinc-100' : 'text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              <span className="font-medium truncate w-full">{a.name}</span>
              <span className="mt-0.5 text-xs text-zinc-500 truncate w-full">
                {a.is_system_preset ? t('aiLibrary.agents.systemPreset') : 'Custom'} · {a.model}
              </span>
            </button>
          );
        })}
      </aside>
      <main className="flex-1 overflow-y-auto p-6">
        {selectedSlug && <AgentEditor slug={selectedSlug} />}
      </main>
    </div>
  );
};

export default AgentsTab;
