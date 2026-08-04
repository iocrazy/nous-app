// frontend/components/AILibrary/AgentWorkbenchTab.tsx
// B2 — "what is this agent doing" (spec 2026-08-02 §B2).
//
// Two columns, 1.5fr / 1fr:
//   left   recent conversations (run groups — a whole chat rolled up)
//   right  waiting-on-you · routines · this week
//
// It used to be a single stack that embedded the whole old dashboard tab —
// three 14-day charts, a cost breakdown and a runs table — so the landing
// view of an agent answered "how has it been trending" before it answered
// "what is it doing and does it need me". Those blocks moved to the Profile
// tab, which is where low-frequency audit material belongs.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { MessageCircleQuestion, Plus } from 'lucide-react';
import { relativeTime } from '../../utils/taskDisplay';
import type { AILibraryAgent, AgentRunGroupItem } from '../../types';
import { listNeedsInput, type NeedsInputItem } from '../../services/issuesService';
import { aiLibraryService, type AgentStatsItem } from '../../services/aiLibraryService';
import { AgentRunsSplit } from './AgentRunsSplit';
import { formatCost } from './AgentDashboardTab';
import { AgentRoutinesTab } from './AgentRoutinesTab';
import { NewRoutineModal } from './NewRoutineModal';

interface AgentWorkbenchTabProps {
  agent: AILibraryAgent;
  slug: string;
  /** Team URL prefix, for the link into the issue list. */
  urlPrefix: string;
}

const Panel: React.FC<{
  title: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, action, children }) => (
  <section className="rounded-lg border border-ink-800 bg-ink-900/20">
    <div className="flex items-center gap-2 px-4 pt-3 pb-2 text-[12px] font-semibold text-ink-400">
      <span className="min-w-0 flex-1">{title}</span>
      {action}
    </div>
    {children}
  </section>
);

const fmtTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${Math.round(n / 1_000)}k` : String(n);

export const AgentWorkbenchTab: React.FC<AgentWorkbenchTabProps> = ({
  agent,
  slug,
  urlPrefix,
}) => {
  const { t } = useTranslation();
  // null = the runs view is closed. A group means the user clicked that
  // conversation; `true` means they used the "all runs" header link.
  const [showRuns, setShowRuns] = useState<AgentRunGroupItem | true | null>(null);
  const [waiting, setWaiting] = useState<NeedsInputItem[]>([]);
  const [groups, setGroups] = useState<AgentRunGroupItem[] | null>(null);
  const [stats, setStats] = useState<AgentStatsItem | null>(null);
  const [newRoutineOpen, setNewRoutineOpen] = useState(false);
  // Bumped after a create so AgentRoutinesTab remounts and refetches — it
  // owns its own list and exposes no reload handle.
  const [routinesKey, setRoutinesKey] = useState(0);

  // The needs-input feed carries `assignee_agent_id` and `identifier`, so this
  // can be the questions themselves rather than a count. A count only says you
  // are late; the question says whether you can clear it in ten seconds.
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
        console.error('[AgentWorkbenchTab] listNeedsInput failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agent.id]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const resp = await aiLibraryService.listAgentRunGroups(slug, 6);
        if (!cancelled) setGroups(resp.items ?? []);
      } catch (err) {
        // Empty list rather than null so the panel shows its empty state
        // instead of a spinner that never resolves.
        console.error('[AgentWorkbenchTab] listAgentRunGroups failed:', err);
        if (!cancelled) setGroups([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const all = await aiLibraryService.getAgentStats(7);
        if (!cancelled) setStats(all[agent.id] ?? null);
      } catch (err) {
        console.error('[AgentWorkbenchTab] getAgentStats failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agent.id]);

  if (showRuns) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setShowRuns(null)}
          className="mb-3 text-xs text-ink-500 hover:text-ink-300"
        >
          ← {t('aiLibrary.agents.backToOverview', 'Back to overview')}
        </button>
        <AgentRunsSplit slug={slug} initialGroup={showRuns === true ? null : showRuns} />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-[1.5fr_1fr]">
      {/* ── left: recent conversations ── */}
      <Panel
        title={t('aiLibrary.agents.workbench.recentConversations', 'Recent conversations')}
        action={
          <button
            type="button"
            onClick={() => setShowRuns(true)}
            data-testid="all-runs-link"
            className="shrink-0 text-[11px] font-medium text-ok hover:underline"
          >
            {t('aiLibrary.agents.workbench.allRuns', 'All runs')} →
          </button>
        }
      >
        {groups != null && groups.length === 0 && (
          <p className="px-4 pb-4 text-[12px] text-ink-600">
            {t('aiLibrary.agents.workbench.noConversations', 'No conversations yet')}
          </p>
        )}
        {(groups ?? []).map((g) => (
          <button
            key={g.group_key}
            type="button"
            onClick={() => setShowRuns(g)}
            data-testid="conversation-row"
            className="flex w-full items-center gap-2.5 border-t border-ink-800/60 px-4 py-2.5 text-left text-[12.5px] transition-colors hover:bg-ink-900/50"
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                g.any_running ? 'animate-pulse bg-agent' : 'bg-ink-600'
              }`}
            />
            <span className="min-w-0 flex-1 truncate text-ink-200">
              {g.latest_output_summary?.trim() || g.trigger}
            </span>
            <span className="shrink-0 whitespace-nowrap text-[11px] tabular-nums text-ink-500">
              {g.any_running
                ? t('aiLibrary.agents.workbench.running', 'Running')
                : t('aiLibrary.agents.runs.turns', {
                    defaultValue: '{{count}} turns',
                    count: g.run_count,
                  })}
              {' · '}
              {relativeTime(g.last_started_at)}
            </span>
          </button>
        ))}
      </Panel>

      {/* ── right: what needs you, what runs itself, how much it cost ── */}
      <div className="flex flex-col gap-4">
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

        <Panel
          title={t('aiLibrary.agents.workbench.routines', 'Routines')}
          action={
            <button
              type="button"
              onClick={() => setNewRoutineOpen(true)}
              data-testid="new-routine"
              className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-ok hover:underline"
            >
              <Plus size={11} />
              {t('aiLibrary.agents.routines.newTask', 'New task')}
            </button>
          }
        >
          <div className="px-1 pb-1">
            <AgentRoutinesTab key={routinesKey} agent={agent} />
          </div>
        </Panel>

        <Panel title={t('aiLibrary.agents.workbench.thisWeek', 'This week')}>
          <div className="flex flex-wrap gap-2 px-4 pb-4" data-testid="week-stats">
            <span className="rounded-full border border-ink-800 px-2.5 py-1 text-[11px] text-ink-400">
              {t('aiLibrary.agents.workbench.statRuns', 'runs')}{' '}
              <b className="tabular-nums text-ink-200">{stats?.runs_7d ?? 0}</b>
            </span>
            <span className="rounded-full border border-ink-800 px-2.5 py-1 text-[11px] text-ink-400">
              {t('aiLibrary.agents.workbench.statTokens', 'tok')}{' '}
              <b className="tabular-nums text-ink-200">{fmtTokens(stats?.tokens_7d ?? 0)}</b>
            </span>
            <span className="rounded-full border border-ink-800 px-2.5 py-1 text-[11px] text-ink-400">
              {t('aiLibrary.agents.workbench.statCost', 'spend')}{' '}
              <b className="tabular-nums text-ink-200">{formatCost(stats?.cost_cents_7d ?? 0)}</b>
            </span>
          </div>
        </Panel>
      </div>

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
