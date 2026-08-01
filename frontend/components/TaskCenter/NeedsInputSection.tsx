/**
 * NeedsInputSection — Task Center "Needs your answer" section (Spec-4
 * needs_input first-class, Task 3).
 *
 * Pinned at the top of TaskCenter. Lists issues parked at `needs_followup`
 * where the agent is waiting on a human reply, with an inline textarea so
 * the user can answer without leaving the panel. Submitting hands the text
 * up to the caller via `onAnswer` — this component owns none of the
 * networking, only the pending/draft UI state. The row disappears once the
 * parent's `items` prop no longer includes it (driven by TaskManagerContext
 * refetching after the reply flips the issue out of needs_followup).
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { getIssue, type NeedsInputItem } from '../../services/issuesService';

interface NeedsInputSectionProps {
  items: NeedsInputItem[];
  onAnswer: (issueId: string, text: string) => Promise<void> | void;
}

export const NeedsInputSection: React.FC<NeedsInputSectionProps> = ({ items, onAnswer }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  if (items.length === 0) return null;

  const handleSubmit = async (issueId: string) => {
    const text = (drafts[issueId] ?? '').trim();
    if (!text || pendingIds.has(issueId)) return;
    setPendingIds((prev) => new Set(prev).add(issueId));
    setDrafts((prev) => ({ ...prev, [issueId]: '' }));
    try {
      await onAnswer(issueId, text);
      // On success the row normally vanishes when `items` refreshes; clear
      // the pending flag too in case the parent keeps the row around briefly.
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(issueId);
        return next;
      });
    } catch (err) {
      console.error(`[NeedsInputSection] onAnswer failed for issue ${issueId}:`, err);
      // Restore the draft so the user doesn't lose what they typed.
      setDrafts((prev) => ({ ...prev, [issueId]: text }));
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(issueId);
        return next;
      });
    }
  };

  // No `identifier` (the "MH-N" human key) rides on the needs-input payload
  // — only the numeric issue_id — so resolve it on click via the existing
  // GET /{issue_id} lookup, then land on the same route TodolistPage /
  // IssuesPage already use for deep links (`/team/:teamId/todolist/:identifier`).
  const handleViewConversation = async (item: NeedsInputItem) => {
    try {
      const issue = await getIssue(Number(item.issue_id));
      const teamId = item.team_id ?? (issue.team_id != null ? String(issue.team_id) : null);
      navigate(teamId ? `/team/${teamId}/todolist/${issue.identifier}` : '/todolist');
    } catch (err) {
      console.error('[NeedsInputSection] failed to resolve issue for deep link:', err);
    }
  };

  return (
    <div className="border-b border-warn-line bg-warn-soft">
      <div className="flex items-center gap-2 px-4 py-2">
        <span className="text-sm font-medium text-warn">{t('taskCenter.needsAnswer')}</span>
        <span className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-warn-line text-warn text-xs font-semibold">
          {items.length}
        </span>
      </div>
      <ul className="divide-y divide-warn-line">
        {items.map((item) => {
          const pending = pendingIds.has(item.issue_id);
          const draft = drafts[item.issue_id] ?? '';
          return (
            <li key={item.issue_id} className="px-4 py-3 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-100 truncate">{item.title}</p>
                  {item.question && (
                    <p className="text-sm text-ink-300 mt-0.5">{item.question}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleViewConversation(item)}
                  className="shrink-0 text-xs text-info hover:underline"
                >
                  {t('taskCenter.viewConversation')}
                </button>
              </div>
              <div className="flex items-center gap-2">
                <textarea
                  value={draft}
                  onChange={(e) =>
                    setDrafts((prev) => ({ ...prev, [item.issue_id]: e.target.value }))
                  }
                  placeholder={t('taskCenter.answerPlaceholder') ?? undefined}
                  disabled={pending}
                  rows={1}
                  className="flex-1 resize-none rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-sm text-ink-100 placeholder:text-ink-500 focus:outline-none focus:ring-1 focus:ring-warn-line disabled:opacity-60"
                />
                <button
                  type="button"
                  onClick={() => handleSubmit(item.issue_id)}
                  disabled={pending || !draft.trim()}
                  className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-md bg-warn-line text-warn hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('taskCenter.answerButton')}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default NeedsInputSection;
