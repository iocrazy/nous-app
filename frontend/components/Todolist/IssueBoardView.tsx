/**
 * Paperclip-style kanban board view (A8.6).
 *
 * One column per IssueStatus, each on an island-2 surface with a status-hued
 * hairline across its top so the columns read as a spectrum. Cards are compact
 * — status icon + priority + ID + title + assignee + last activity, plus an
 * amber "running" pulse when an agent is working the issue. Click a card →
 * navigate to detail (same target as the list rows).
 */

import React, { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { UiIssue } from './types';
import type { IssueStatus } from '../../services/issuesService';
import { IssueStatusIcon, STATUS_ORDER, STATUS_LABEL, PriorityIcon } from './IssueStatusIcon';
import { STATUS_CONFIG } from './issueConfig';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueBoardViewProps {
  issues: UiIssue[];
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-ink-600', size = 18 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[9px] font-semibold text-ink-50 ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

const BoardCard: React.FC<{ issue: UiIssue; teamId: string }> = ({ issue, teamId }) => {
  const initials = issue.assignee?.name.slice(0, 2).toUpperCase() ?? (issue.assignee_user_label?.slice(0, 2).toUpperCase() ?? '');
  const isLive = !!issue.raw.dbos_workflow_id && issue.status !== 'done' && issue.status !== 'cancelled';
  return (
    <Link
      to={`/team/${teamId}/todolist/${issue.identifier}`}
      className="block p-2 rounded-lg border border-line bg-island hover:border-line-strong transition"
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="font-mono text-[9px] text-ink-500 uppercase tracking-wider">{issue.identifier}</span>
        {isLive && (
          <span className="inline-flex items-center gap-1 text-[9px] text-amber-400" title="An agent is working on this">
            <span className="w-1 h-1 rounded-full bg-amber-400 animate-pulse" />
            running
          </span>
        )}
        <span title={issue.priority} className="ml-auto"><PriorityIcon priority={issue.priority} /></span>
      </div>
      <div className="text-[12px] text-ink-100 mb-2 line-clamp-2 leading-snug">{issue.title}</div>
      <div className="flex items-center gap-1.5 text-[9px] text-ink-500">
        {issue.project && (
          <span className="inline-flex items-center gap-1 px-1 py-0.5 rounded bg-ink-800 text-ink-400">
            <span className={`w-1 h-1 rounded-full ${issue.project.color ?? 'bg-ink-500'}`} />
            {issue.project.name}
          </span>
        )}
        {(issue.assignee || issue.assignee_user_label) && (
          <AgentAvatar initials={initials} color={issue.assignee?.avatar_color} />
        )}
        <span className="ml-auto tabular-nums">{relativeTime(issue.last_activity_at)}</span>
      </div>
    </Link>
  );
};

export const IssueBoardView: React.FC<IssueBoardViewProps> = ({ issues }) => {
  const { teamId } = useParams<{ teamId: string }>();

  const grouped = useMemo(() => {
    const map = new Map<IssueStatus, UiIssue[]>();
    for (const s of STATUS_ORDER) map.set(s, []);
    for (const i of issues) {
      const bucket = map.get(i.status);
      if (bucket) bucket.push(i);
    }
    return STATUS_ORDER.map((s) => ({ status: s, items: map.get(s) ?? [] }));
  }, [issues]);

  return (
    <div className="flex gap-3 px-4 py-3 overflow-x-auto h-full">
      {grouped.map((g) => (
        <div key={g.status} className="flex-shrink-0 w-[260px] flex flex-col bg-island-2 border border-line rounded-xl overflow-hidden max-h-full">
          <div className="relative flex items-center gap-2 px-2.5 py-2 border-b border-line sticky top-0 bg-island-2 z-10">
            <span className={`absolute top-0 left-3 right-3 h-0.5 rounded-b opacity-70 ${STATUS_CONFIG[g.status].tick}`} />
            <IssueStatusIcon status={g.status} size={11} />
            <span className="text-[11px] font-medium text-ink-300 uppercase tracking-wider">
              {STATUS_LABEL[g.status]}
            </span>
            <span className="text-[10px] text-ink-500 ml-auto tabular-nums">{g.items.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
            {g.items.length === 0 ? (
              <div className="border border-dashed border-line-strong rounded-lg py-6 px-2 text-center text-[11px] text-ink-500">
                Drop issues here
              </div>
            ) : (
              g.items.map((issue) => (
                <BoardCard key={issue.id} issue={issue} teamId={teamId ?? ''} />
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
