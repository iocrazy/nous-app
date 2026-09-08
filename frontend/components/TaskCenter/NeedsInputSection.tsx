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
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { getIssue, type NeedsInputItem } from '../../services/issuesService';
import { AgentNotDispatchedError } from '../../services/issueMessageService';
import { QuestionCard } from '../Todolist/QuestionCard';
import { questionFromNeedsInputItem } from '../Todolist/questionTypes';

interface NeedsInputSectionProps {
  items: NeedsInputItem[];
  /** `answerTo` (phase 2a) is the typed question's id when the row carries
   *  one — the caller sends it as `answer_to`. */
  onAnswer: (issueId: string, text: string, answerTo?: string) => Promise<void> | void;
}

export const NeedsInputSection: React.FC<NeedsInputSectionProps> = ({ items, onAnswer }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  // Finding 5 (final review): set only when a reply POSTed successfully but
  // no agent turn was actually dispatched (legacy/no-op paths) — distinct
  // from a network/backend failure, which restores the draft for retry
  // instead. Cleared on the next submit attempt for that row.
  const [dispatchErrors, setDispatchErrors] = useState<Record<string, boolean>>({});

  // A resolved `onAnswer` promise only means the POST landed — it does NOT
  // guarantee the backend's needs_followup flip has landed before the next
  // `items` snapshot arrives (that flip + the refetch are two separate
  // round trips). So pending is cleared ONLY by the row actually leaving
  // `items` (the real completion signal), never by promise resolution.
  // Without this, a slow flip would re-enable the card with an empty
  // draft while the row is still present, making the answer look like it
  // vanished. If an issue_id later REAPPEARS after having left `items`, it
  // reads as a fresh ask (agent asked again, or the flip didn't stick) —
  // pruning stale pending entries below means a reappearing id is simply
  // not in `pendingIds` anymore, so it renders answerable, not stuck.
  useEffect(() => {
    const currentIds = new Set(items.map((i) => i.issue_id));
    setPendingIds((prev) => {
      let changed = false;
      const next = new Set<string>();
      prev.forEach((id) => {
        if (currentIds.has(id)) {
          next.add(id);
        } else {
          changed = true;
        }
      });
      return changed ? next : prev;
    });
    // Same reappearing-id logic as pendingIds above: a row that left `items`
    // and comes back reads as a fresh ask, not "still showing the old
    // no-dispatch warning".
    setDispatchErrors((prev) => {
      let changed = false;
      const next: Record<string, boolean> = {};
      for (const id of Object.keys(prev)) {
        if (currentIds.has(id)) {
          next[id] = prev[id];
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [items]);

  if (items.length === 0) return null;

  const handleSubmit = async (issueId: string) => {
    const text = (drafts[issueId] ?? '').trim();
    if (!text || pendingIds.has(issueId)) return;
    setPendingIds((prev) => new Set(prev).add(issueId));
    setDrafts((prev) => ({ ...prev, [issueId]: '' }));
    setDispatchErrors((prev) => {
      if (!(issueId in prev)) return prev;
      const next = { ...prev };
      delete next[issueId];
      return next;
    });
    try {
      await onAnswer(issueId, text);
      // Deliberately NOT clearing pendingIds here — see the comment on the
      // items-sync effect above. The row's removal from `items` is what
      // clears it.
    } catch (err) {
      console.error(`[NeedsInputSection] onAnswer failed for issue ${issueId}:`, err);
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(issueId);
        return next;
      });
      if (err instanceof AgentNotDispatchedError) {
        // The message WAS saved server-side (no data lost) — just no turn
        // started. Re-typing it would post a duplicate, so the draft is
        // left empty; the card gets an inline warning instead of silently
        // sitting pending forever with no status flip to clear it.
        setDispatchErrors((prev) => ({ ...prev, [issueId]: true }));
        return;
      }
      // Network/backend failure: nothing was saved — restore the draft so
      // the user doesn't lose what they typed, and re-enable the card so
      // they can retry.
      setDrafts((prev) => ({ ...prev, [issueId]: text }));
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
          // Any typed question (buttons or open-ended) answers through the
          // card so `answer_to` always travels; the chip only counts options.
          const typed = questionFromNeedsInputItem(item);
          const hasOptions = !!typed && typed.options.length > 0;
          return (
            <li key={item.issue_id} className="px-4 py-3 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-100 truncate">{item.title}</p>
                  {item.question && (
                    <p className="text-sm text-ink-300 mt-0.5">{item.question}</p>
                  )}
                  {hasOptions && (
                    <span
                      data-testid="needs-input-pick-one"
                      className="mt-1 inline-flex items-center rounded-full border border-warn-line px-2 py-0.5 text-[11px] text-warn"
                    >
                      {t('question.pickOne', { count: typed!.options.length })}
                    </span>
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
              {typed ? (
                <QuestionCard
                  compact
                  disabled={pending}
                  question={typed}
                  onAnswer={async (value, answerTo) => {
                    if (pendingIds.has(item.issue_id)) return;
                    setPendingIds((prev) => new Set(prev).add(item.issue_id));
                    setDispatchErrors((prev) => {
                      if (!(item.issue_id in prev)) return prev;
                      const next = { ...prev };
                      delete next[item.issue_id];
                      return next;
                    });
                    try {
                      await onAnswer(item.issue_id, value, answerTo);
                      // pending clears when the row leaves `items` (see above)
                    } catch (err) {
                      setPendingIds((prev) => {
                        const next = new Set(prev);
                        next.delete(item.issue_id);
                        return next;
                      });
                      if (err instanceof AgentNotDispatchedError) {
                        setDispatchErrors((prev) => ({ ...prev, [item.issue_id]: true }));
                        return;
                      }
                      throw err; // QuestionCard shows the typed error inline
                    }
                  }}
                />
              ) : (
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
              )}
              {dispatchErrors[item.issue_id] && (
                <p className="text-xs text-warn">{t('taskCenter.answerNotDispatched')}</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default NeedsInputSection;
