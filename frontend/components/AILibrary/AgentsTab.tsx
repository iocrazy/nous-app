// frontend/components/AILibrary/AgentsTab.tsx
// Selected-agent editor only. The agent list + "New Agent" button live in
// AILibrarySidebar (the outer secondary sidebar). Rendering them here too
// produced a duplicated rail; that mid-column was removed in favor of a
// single source of truth.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentEditor } from './AgentEditor';
import { AILibraryTabs } from './AILibraryTabs';

interface AgentsTabProps {
  /** Selected agent slug (driven by URL via AgentsPage). */
  slug?: string | null;
  /** Called when the user forks an agent and we need to navigate to the
   *  new slug. AgentsPage wires this to react-router navigate. */
  onSlugChange?: (slug: string) => void;
  /** Called after the selected agent is deleted — navigate back to the list. */
  onAgentDeleted?: () => void;
}

export const AgentsTab: React.FC<AgentsTabProps> = ({ slug, onSlugChange, onAgentDeleted }) => {
  const { t } = useTranslation();
  const [agentCount, setAgentCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // We still need to know whether *any* agent exists so the empty-state
  // copy renders correctly when the user lands on /ai-library/agents
  // without a slug. The actual list lives in AILibrarySidebar. The count
  // also feeds the tab strip below, so it costs no extra request.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (!cancelled) setAgentCount(list.length);
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
          {t('aiLibrary.agents.listLoadError')}: {error}
        </div>
      </div>
    );
  }

  if (!slug) {
    return (
      // `min-h-[60vh]` rather than `h-full`: nothing in this page's chain pins
      // a height any more (see AgentsPage), so `h-full` would resolve to auto
      // and collapse the centring to a line of text under the header.
      <div className="flex min-h-[60vh] items-center justify-center p-6 text-sm text-ink-500">
        {agentCount === 0
          ? t('aiLibrary.agents.selectAgent', 'No agents yet — use + to create one.')
          : t('aiLibrary.agents.pickFromSidebar', 'Pick an agent from the sidebar.')}
      </div>
    );
  }

  return (
    // NO scroll container here. This was `h-full overflow-y-auto`, nested
    // inside AILibraryLayout's own `flex-1 overflow-y-auto px-8`: `h-full`
    // pinned it to exactly the outer height, so the inner box was the one that
    // scrolled and the outer never did. Both of the layout bugs reported
    // against this page came out of that one line —
    //
    //   - the scrollbar sat at the INNER box's right edge, inside the page
    //     gutter, so on the Persona tab it ran flush against the attributes
    //     card instead of at the page margin;
    //   - it appeared per tab, not per page (Workbench content fits, Persona /
    //     Permissions / Cost / Profile do not), so every tab switch jogged the
    //     column sideways by the scrollbar's width — the "ghost" gap between
    //     the Permissions and Profile tabs. The tab strip itself is a plain
    //     5-button map with no stray nodes; it was never the cause.
    //
    // One scroll container per scrollable region: the layout's.
    <div className="pt-6 pb-8">
      {/* The agent detail page had no strip at all, so opening an agent was a
          one-way trip — the only route back to the gallery was the browser
          button. Same mount and same rule as the skill editor. */}
      <div className="px-1 pb-4">
        <AILibraryTabs active="agents" agentCount={agentCount} placement="detail" />
      </div>

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
        onAgentDeleted={onAgentDeleted}
      />
    </div>
  );
};

export default AgentsTab;
