// frontend/components/AILibrary/AgentWorkbenchTab.tsx
// B2 — "what is this agent doing" (spec 2026-08-02 §B2).
//
// Absorbs three of the old eight sub-tabs (Dashboard / Runs / Routines) into
// the landing view of an agent's detail page: current activity, anything
// waiting on the human, and the schedule that drives it.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MessageCircleQuestion } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentDashboardTab } from './AgentDashboardTab';
import { AgentRunsSplit } from './AgentRunsSplit';
import { AgentRoutinesTab } from './AgentRoutinesTab';

interface AgentWorkbenchTabProps {
  agent: AILibraryAgent;
  slug: string;
  /** Team URL prefix, for the link into the issue list. */
  urlPrefix: string;
}

export const AgentWorkbenchTab: React.FC<AgentWorkbenchTabProps> = ({
  agent,
  slug,
  urlPrefix,
}) => {
  const { t } = useTranslation();
  const [showRuns, setShowRuns] = useState(false);
  const [waiting, setWaiting] = useState(0);

  // Waiting-reply count comes from the batch stats endpoint. It is a COUNT,
  // not a list: `NeedsInputItem` carries no agent dimension, so a per-issue
  // list here would need a new endpoint — the per-issue entry point already
  // exists in the issue list, which is where the link goes.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const stats = await aiLibraryService.getAgentStats(7);
        if (!cancelled) setWaiting(stats[agent.id]?.needs_input_count ?? 0);
      } catch (err) {
        console.error('[AgentWorkbenchTab] getAgentStats failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agent.id]);

  return (
    <div className="space-y-5">
      {waiting > 0 && (
        <div
          className="flex items-center gap-2 rounded-lg border border-warn-line bg-warn-soft px-3 py-2 text-[13px] text-warn"
          data-testid="waiting-replies"
        >
          <MessageCircleQuestion size={15} className="shrink-0" />
          <span className="min-w-0 flex-1">
            {t('aiLibrary.agents.waitingReplies', '{{count}} issue(s) waiting for your reply', {
              count: waiting,
            })}
          </span>
          <a
            href={`${urlPrefix}/todolist`}
            className="shrink-0 underline"
          >
            {t('aiLibrary.agents.viewInIssues', 'View in Issues')}
          </a>
        </div>
      )}

      {showRuns ? (
        <div>
          <button
            type="button"
            onClick={() => setShowRuns(false)}
            className="mb-3 text-xs text-ink-500 hover:text-ink-300"
          >
            ← {t('aiLibrary.agents.backToOverview', 'Back to overview')}
          </button>
          <AgentRunsSplit slug={slug} />
        </div>
      ) : (
        <AgentDashboardTab slug={slug} onOpenRuns={() => setShowRuns(true)} />
      )}

      <AgentRoutinesTab agent={agent} />
    </div>
  );
};

export default AgentWorkbenchTab;
