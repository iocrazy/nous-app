// frontend/components/AILibrary/AgentsTab.tsx
// Selected-agent editor only. The agent list + "New Agent" button live in
// AILibrarySidebar (the outer secondary sidebar). Rendering them here too
// produced a duplicated rail; that mid-column was removed in favor of a
// single source of truth.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentEditor } from './AgentEditor';

interface AgentsTabProps {
  /** Selected agent slug (driven by URL via AgentsPage). */
  slug?: string | null;
  /** Called when the user forks an agent and we need to navigate to the
   *  new slug. AgentsPage wires this to react-router navigate. */
  onSlugChange?: (slug: string) => void;
}

export const AgentsTab: React.FC<AgentsTabProps> = ({ slug, onSlugChange }) => {
  const { t } = useTranslation();
  const [hasAgents, setHasAgents] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  // We still need to know whether *any* agent exists so the empty-state
  // copy renders correctly when the user lands on /ai-library/agents
  // without a slug. The actual list lives in AILibrarySidebar.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (!cancelled) setHasAgents(list.length > 0);
      } catch (err) {
        if (cancelled) return;
        console.error('[AgentsTab] listAgents failed:', err);
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          Failed to load agents: {error}
        </div>
      </div>
    );
  }

  if (!slug) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-zinc-500">
        {hasAgents === false
          ? t('aiLibrary.agents.selectAgent', 'No agents yet — use + to create one.')
          : t('aiLibrary.agents.pickFromSidebar', 'Pick an agent from the sidebar.')}
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <AgentEditor
        // ``key={slug}`` forces a full remount when the slug changes so a
        // pending in-flight fetch from the previous slug can't land late and
        // setAgent() the old data on top of the new one — caught when the
        // skill fork flow showed stale Script Outline content after URL
        // navigated to the fresh fork (parent's ``await loadAgents()`` race).
        key={slug}
        slug={slug}
        onAgentForked={(newSlug) => {
          if (onSlugChange) onSlugChange(newSlug);
        }}
      />
    </div>
  );
};

export default AgentsTab;
