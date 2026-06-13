/**
 * Paperclip-style kanban board view (A8.6).
 *
 * One column per IssueStatus. Cards in each column are compact —
 * status icon + priority + ID + title + assignee + last activity.
 * Click a card → navigate to detail (same target as the list rows).
 */

import React, { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { UiIssue } from './types';
import type { IssueStatus } from '../../services/issuesService';
import { IssueStatusIcon, STATUS_ORDER, STATUS_LABEL, PriorityIcon } from './IssueStatusIcon';
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
  return (
    <Link
      to={`/team/${teamId}/todolist/${issue.identifier}`}
      className="block p-2 mb-1.5 rounded border border-ink-800 bg-ink-900/60 hover:border-ink-700 hover:bg-ink-800/60 transition"
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="font-mono text-[9px] text-ink-500 uppercase tracking-wider">{issue.identifier}</span>
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
        <span className="ml-auto">{relativeTime(issue.last_activity_at)}</span>
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
        <div key={g.status} className="flex-shrink-0 w-[260px] flex flex-col">
          <div className="flex items-center gap-2 px-2 py-1.5 mb-2 border-b border-ink-800/80 sticky top-0 bg-ink-950/40 z-10">
            <IssueStatusIcon status={g.status} size={11} />
            <span className="text-[11px] font-medium text-ink-300 uppercase tracking-wider">
              {STATUS_LABEL[g.status]}
            </span>
            <span className="text-[10px] text-ink-500 ml-auto">{g.items.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto pr-1">
            {g.items.length === 0 ? (
              <div className="text-[10px] text-ink-600 italic px-2 py-3 text-center">empty</div>
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
