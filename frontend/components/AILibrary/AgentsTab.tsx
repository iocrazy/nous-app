// frontend/components/AILibrary/AgentsTab.tsx
// Left rail: list of agents (system presets + custom) + "New Agent" button.
// Right pane: AgentEditor for the selected agent.
//
// Phase 2 PR 2.9: agents are grouped in the left rail by scope —
// System Presets → My Private → each Team → each Project.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentEditor } from './AgentEditor';
import { NewAgentModal } from './NewAgentModal';
import { ScopedList, groupByScope } from './ScopedList';
import type { ScopedGroup, ScopedLabels } from './ScopedList';

/**
 * Slot an agent into a scope-derived group. Thin wrapper around the shared
 * ``groupByScope`` helper so unit tests can keep exercising the groupings
 * via a stable import.
 *
 * Exported separately so it can be unit-tested without a React render.
 */
export function groupAgentsByScope(
  agents: AILibraryAgent[],
  labels: ScopedLabels,
): ScopedGroup<AILibraryAgent>[] {
  return groupByScope(agents, (a) => a.is_system_preset, labels);
}

export const AgentsTab: React.FC = () => {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNewAgentModal, setShowNewAgentModal] = useState(false);

  const loadAgents = React.useCallback(async () => {
    try {
      setLoading(true);
      const list = await aiLibraryService.listAgents();
      setAgents(list);
      if (list.length > 0 && !selectedSlug) {
        setSelectedSlug(list[0].slug);
      }
    } catch (err) {
      console.error('[AgentsTab] listAgents failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [selectedSlug]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (cancelled) return;
        setAgents(list);
        if (list.length > 0) setSelectedSlug(list[0].slug);
      } catch (err) {
        if (cancelled) return;
        console.error('[AgentsTab] listAgents failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreated = async (newSlug: string) => {
    setShowNewAgentModal(false);
    setSelectedSlug(newSlug);
    await loadAgents();
  };

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

  const labels: ScopedLabels = {
    systemPresets: t('aiLibrary.agents.groupSystemPresets', 'System Presets'),
    privateLabel: t('aiLibrary.agents.groupPrivate', 'My Private'),
    teamLabel: (name: string) =>
      t('aiLibrary.agents.groupTeam', 'Team: {{name}}', { name }),
    projectLabel: (name: string) =>
      t('aiLibrary.agents.groupProject', 'Project: {{name}}', { name }),
  };
  const hasAgents = agents.length > 0;

  return (
    <div className="flex h-full">
      <aside className="w-64 flex-shrink-0 overflow-y-auto border-r border-zinc-800 bg-zinc-950/40">
        <div className="border-b border-zinc-800 p-3">
          <button
            type="button"
            onClick={() => setShowNewAgentModal(true)}
            className="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500"
          >
            + {t('aiLibrary.agents.newAgent', 'New Agent')}
          </button>
        </div>
        {!hasAgents ? (
          <div className="p-4 text-xs text-zinc-500">
            {t('aiLibrary.agents.selectAgent', 'No agents yet.')}
          </div>
        ) : (
          <ScopedList
            items={agents}
            isSystemPreset={(a) => a.is_system_preset}
            labels={labels}
            headerVariant="sidebar"
            renderItem={(a) => {
              const active = selectedSlug === a.slug;
              return (
                <button
                  onClick={() => setSelectedSlug(a.slug)}
                  className={`flex w-full flex-col items-start border-b border-zinc-800/40 px-4 py-3 text-left transition-colors ${
                    active
                      ? 'bg-indigo-500/10 text-zinc-100'
                      : 'text-zinc-300 hover:bg-zinc-800/50'
                  }`}
                >
                  <span className="font-medium truncate w-full">{a.name}</span>
                  <span className="mt-0.5 text-xs text-zinc-500 truncate w-full">
                    {a.model}
                  </span>
                </button>
              );
            }}
          />
        )}
      </aside>
      <main className="flex-1 overflow-y-auto p-6">
        {selectedSlug && (
          <AgentEditor
            slug={selectedSlug}
            onAgentForked={async (newSlug) => {
              await loadAgents();
              setSelectedSlug(newSlug);
            }}
          />
        )}
      </main>
      {showNewAgentModal && (
        <NewAgentModal
          existingAgents={agents}
          onClose={() => setShowNewAgentModal(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
};

export default AgentsTab;
