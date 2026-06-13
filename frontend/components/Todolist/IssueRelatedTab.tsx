/**
 * Related work tab — sub-issues + parent breadcrumb (A8.7).
 *
 * For now: just children + parent. Future: blocked-by / blocks links,
 * attached documents, agent_runs gallery.
 */

import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { UiIssue } from './types';
import { listSubIssues, getIssue, type Issue } from '../../services/issuesService';
import { IssueStatusIcon, PriorityIcon } from './IssueStatusIcon';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueRelatedTabProps {
  issue: UiIssue;
}

export const IssueRelatedTab: React.FC<IssueRelatedTabProps> = ({ issue }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [children, setChildren] = useState<Issue[]>([]);
  const [parent, setParent] = useState<Issue | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const subs = await listSubIssues(issue.id);
        if (!cancelled) setChildren(subs);
      } catch {
        /* ignore */
      }
      if (issue.parent_id) {
        try {
          const p = await getIssue(issue.parent_id);
          if (!cancelled) setParent(p);
        } catch {
          /* ignore */
        }
      } else {
        setParent(null);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [issue.id, issue.parent_id]);

  if (loading) {
    return <div className="text-[14px] text-ink-500 italic px-4 py-12 text-center">Loading related work…</div>;
  }

  return (
    <div className="px-4 py-4 space-y-5">
      {parent && (
        <section>
          <h3 className="text-[12px] font-semibold uppercase tracking-wider text-ink-500 mb-2">Parent</h3>
          <Link
            to={`/team/${teamId}/todolist/${parent.identifier}`}
            className="block px-3 py-2 rounded border border-ink-800 bg-ink-900/50 hover:bg-ink-800/60 transition"
          >
            <div className="flex items-center gap-2 text-[13px]">
              <IssueStatusIcon status={parent.status} size={13} />
              <span className="font-mono text-[12px] text-ink-500 uppercase">{parent.identifier}</span>
              <span className="text-ink-200">{parent.title}</span>
              <span className="ml-auto text-[12px] text-ink-500">{relativeTime(parent.updated_at ?? parent.created_at)}</span>
            </div>
          </Link>
        </section>
      )}

      <section>
        <h3 className="text-[12px] font-semibold uppercase tracking-wider text-ink-500 mb-2">
          Sub-issues ({children.length})
        </h3>
        {children.length === 0 ? (
          <div className="text-[13px] text-ink-500 italic px-3 py-4 border border-dashed border-ink-800/80 rounded text-center">
            No sub-issues yet. Use the "+ New Sub-issue" button above to add one.
          </div>
        ) : (
          <ul className="space-y-1">
            {children.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/team/${teamId}/todolist/${c.identifier}`}
                  className="flex items-center gap-2 px-3 py-1.5 rounded border border-ink-800/80 bg-ink-900/40 hover:bg-ink-800/60 transition text-[13px]"
                >
                  <IssueStatusIcon status={c.status} size={12} />
                  <span><PriorityIcon priority={c.priority} /></span>
                  <span className="font-mono text-[12px] text-ink-500 uppercase">{c.identifier}</span>
                  <span className="flex-1 truncate text-ink-200">{c.title}</span>
                  <span className="text-[12px] text-ink-500">{relativeTime(c.updated_at ?? c.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="text-[12px] text-ink-600 italic">
        Attached documents and blocked-by links land in a follow-up — schema not in place yet.
      </section>
    </div>
  );
};
