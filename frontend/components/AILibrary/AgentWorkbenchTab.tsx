// frontend/components/AILibrary/AgentWorkbenchTab.tsx
// B2 — "what is this agent doing" (spec 2026-08-02 §B2).
//
// Absorbs three of the old eight sub-tabs (Dashboard / Runs / Routines) into
// the landing view of an agent's detail page: current activity, anything
// waiting on the human, and the schedule that drives it.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { MessageCircleQuestion, Plus } from 'lucide-react';
import { relativeTime } from '../../utils/taskDisplay';
import type { AILibraryAgent } from '../../types';
import { listNeedsInput, type NeedsInputItem } from '../../services/issuesService';
import { AgentDashboardTab } from './AgentDashboardTab';
import { AgentRunsSplit } from './AgentRunsSplit';
import { AgentRoutinesTab } from './AgentRoutinesTab';
import { NewRoutineModal } from './NewRoutineModal';

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
  const [waiting, setWaiting] = useState<NeedsInputItem[]>([]);
  const [newRoutineOpen, setNewRoutineOpen] = useState(false);
  // Bumped after a create so AgentRoutinesTab remounts and refetches — it
  // owns its own list and exposes no reload handle.
  const [routinesKey, setRoutinesKey] = useState(0);

  // The needs-input feed now carries `assignee_agent_id` and `identifier`, so
  // this can be the questions themselves rather than the count the batch stats
  // endpoint used to hand over. A count only says you are late; the question
  // says whether you can clear it in ten seconds.
  //
  // Rows without an identifier are dropped: the detail route is keyed by it,
  // and a card whose "go answer" button goes nowhere is worse than no card.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const resp = await listNeedsInput();
        if (cancelled) return;
        setWaiting(
          (resp.items ?? []).filter(
            (i) => i.assignee_agent_id === agent.id && !!i.identifier,
          ),
        );
      } catch (err) {
        // Degrade to no card — this panel sits above the whole workbench.
        console.error('[AgentWorkbenchTab] listNeedsInput failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agent.id]);

  return (
    <div className="space-y-5">
      {waiting.length > 0 && (
        <section
          className="rounded-lg border border-warn-line bg-warn-soft px-3 py-2.5"
          data-testid="waiting-replies"
        >
          <div className="flex items-center gap-2 text-[12px] font-semibold text-warn">
            <MessageCircleQuestion size={14} className="shrink-0" />
            <span className="min-w-0 flex-1">
              {t('aiLibrary.agents.waitingReplies', '{{count}} issue(s) waiting for your reply', {
                count: waiting.length,
              })}
            </span>
            <a href={`${urlPrefix}/todolist`} className="shrink-0 font-normal underline">
              {t('aiLibrary.agents.viewInIssues', 'View in Issues')}
            </a>
          </div>
          <ul className="mt-2 space-y-2">
            {waiting.map((issue) => (
              <li
                key={issue.issue_id}
                data-testid="waitcard"
                className="rounded-md border border-warn-line bg-ink-950/30 px-3 py-2"
              >
                {/* The agent's own words. Falling back to the title keeps the
                    card useful when a run parked without giving a reason. */}
                <p className="text-[13px] leading-relaxed text-ink-200 break-words">
                  {issue.question ?? issue.title}
                </p>
                <div className="mt-1.5 flex items-center gap-2 text-[11px] text-ink-500">
                  <span className="truncate">
                    {issue.identifier} · {relativeTime(issue.asked_at)}
                  </span>
                  <Link
                    to={`${urlPrefix}/todolist/${issue.identifier}`}
                    data-testid="waitcard-answer-link"
                    className="ml-auto shrink-0 font-medium text-warn hover:underline"
                  >
                    {t('aiLibrary.agents.goAnswer', 'Go answer')} →
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        </section>
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

      <section>
        <div className="mb-2 flex items-center justify-end">
          <button
            type="button"
            onClick={() => setNewRoutineOpen(true)}
            data-testid="new-routine"
            className="inline-flex items-center gap-1 rounded-md border border-ink-700 px-2.5 py-1 text-[12px] text-ink-200 hover:bg-ink-800/60"
          >
            <Plus size={12} />
            {t('aiLibrary.agents.routines.newTask', 'New task')}
          </button>
        </div>
        <AgentRoutinesTab key={routinesKey} agent={agent} />
      </section>

      {newRoutineOpen && (
        <NewRoutineModal
          agent={agent}
          onClose={() => setNewRoutineOpen(false)}
          onCreated={() => {
            setNewRoutineOpen(false);
            setRoutinesKey((k) => k + 1);
          }}
        />
      )}
    </div>
  );
};

export default AgentWorkbenchTab;
