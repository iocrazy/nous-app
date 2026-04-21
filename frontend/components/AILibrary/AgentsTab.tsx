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

/**
 * One rendered section in the sidebar. ``key`` doubles as the React key and
 * a stable identifier for test selectors; it is NOT shown to the user.
 */
interface AgentGroup {
  key: string;
  label: string;
  agents: AILibraryAgent[];
}

/**
 * Slot an agent into a scope-derived group. Ordering priority:
 *   1. System Presets (is_system_preset = true)
 *   2. My Private     (user-owned, no team / no project)
 *   3. Each Team      (one group per distinct team_id)
 *   4. Each Project   (one group per distinct project_id)
 *
 * Exported separately so it can be unit-tested without a React render.
 */
export function groupAgentsByScope(
  agents: AILibraryAgent[],
  labels: {
    systemPresets: string;
    privateLabel: string;
    teamLabel: (name: string) => string;
    projectLabel: (name: string) => string;
  },
): AgentGroup[] {
  const presets: AILibraryAgent[] = [];
  const privateOnes: AILibraryAgent[] = [];
  const byTeam = new Map<number, { name: string; agents: AILibraryAgent[] }>();
  const byProject = new Map<number, { name: string; agents: AILibraryAgent[] }>();

  for (const a of agents) {
    if (a.is_system_preset) {
      presets.push(a);
      continue;
    }
    if (a.team_id != null) {
      const entry = byTeam.get(a.team_id) ?? {
        name: a.team_name ?? String(a.team_id),
        agents: [],
      };
      entry.agents.push(a);
      byTeam.set(a.team_id, entry);
      continue;
    }
    if (a.project_id != null) {
      const entry = byProject.get(a.project_id) ?? {
        name: a.project_name ?? String(a.project_id),
        agents: [],
      };
      entry.agents.push(a);
      byProject.set(a.project_id, entry);
      continue;
    }
    privateOnes.push(a);
  }

  const groups: AgentGroup[] = [];
  if (presets.length > 0) {
    groups.push({ key: 'system', label: labels.systemPresets, agents: presets });
  }
  if (privateOnes.length > 0) {
    groups.push({ key: 'private', label: labels.privateLabel, agents: privateOnes });
  }
  for (const [teamId, entry] of byTeam) {
    groups.push({
      key: `team:${teamId}`,
      label: labels.teamLabel(entry.name),
      agents: entry.agents,
    });
  }
  for (const [projectId, entry] of byProject) {
    groups.push({
      key: `project:${projectId}`,
      label: labels.projectLabel(entry.name),
      agents: entry.agents,
    });
  }
  return groups;
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

  const groups = groupAgentsByScope(agents, {
    systemPresets: t('aiLibrary.agents.groupSystemPresets', 'System Presets'),
    privateLabel: t('aiLibrary.agents.groupPrivate', 'My Private'),
    teamLabel: (name: string) =>
      t('aiLibrary.agents.groupTeam', 'Team: {{name}}', { name }),
    projectLabel: (name: string) =>
      t('aiLibrary.agents.groupProject', 'Project: {{name}}', { name }),
  });

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
        {groups.length === 0 ? (
          <div className="p-4 text-xs text-zinc-500">
            {t('aiLibrary.agents.selectAgent', 'No agents yet.')}
          </div>
        ) : (
          groups.map((group) => (
            <div key={group.key}>
              <div className="sticky top-0 z-10 bg-zinc-950/95 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 border-b border-zinc-800/60">
                {group.label}
              </div>
              {group.agents.map((a) => {
                const active = selectedSlug === a.slug;
                return (
                  <button
                    key={a.slug}
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
              })}
            </div>
          ))
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
