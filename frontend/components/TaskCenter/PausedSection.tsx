/**
 * PausedSection — Task Center "Paused" section (phase 2a §4).
 *
 * Lists the issues a person paused (`GET /issues/paused`), each with a Resume
 * button. Owns its own fetch (mount, every PAUSED_POLL_MS, and on every
 * pause/resume signalled from elsewhere); the row leaves the list when the
 * refetch no longer returns it. Overflow is said ("50+"), not hidden. Resume is a typed path: the
 * server says what it did (`reason`), and a failure stays on the row.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PauseCircle } from 'lucide-react';

import { listPaused, resumeIssue, type PausedIssueItem } from '../../services/issuesService';
import { controlErrorText } from '../Todolist/issueControlErrors';
import { notifyIssuePauseChanged, subscribeIssuePauseChanged } from './issuePauseSignal';

/** Poll cadence; a pause/resume from anywhere in the app also refetches at
 *  once through issuePauseSignal. */
export const PAUSED_POLL_MS = 30_000;

export const PausedSection: React.FC = () => {
  const { t } = useTranslation();
  const [items, setItems] = useState<PausedIssueItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const res = await listPaused();
      setItems(res.items);
      setHasMore(res.has_more);
    } catch (err) {
      // Non-fatal: the rest of the Task Center still renders.
      console.error('[PausedSection] list paused failed', err);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), PAUSED_POLL_MS);
    const unsubscribe = subscribeIssuePauseChanged(() => void refresh());
    return () => {
      clearInterval(id);
      unsubscribe();
    };
  }, [refresh]);

  const resume = async (item: PausedIssueItem) => {
    if (busy.has(item.issue_id)) return;
    setBusy((prev) => new Set(prev).add(item.issue_id));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[item.issue_id];
      return next;
    });
    try {
      await resumeIssue(Number(item.issue_id));
      notifyIssuePauseChanged(Number(item.issue_id));
      await refresh();
    } catch (err) {
      console.error(`[PausedSection] resume failed for issue ${item.issue_id}:`, err);
      setErrors((prev) => ({ ...prev, [item.issue_id]: controlErrorText(err, t) }));
    } finally {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(item.issue_id);
        return next;
      });
    }
  };

  if (items.length === 0) return null;

  return (
    <div className="border-b border-info-line bg-info-soft" data-testid="paused-section">
      <div className="flex items-center gap-2 px-4 py-2">
        <PauseCircle size={14} className="text-info" />
        <span className="text-sm font-medium text-info">{t('taskCenter.paused', 'Paused')}</span>
        <span data-testid="paused-count" className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-info-soft ring-1 ring-info-line text-info text-xs font-semibold">
          {hasMore ? `${items.length}+` : items.length}
        </span>
      </div>
      <ul className="divide-y divide-info-line">
        {items.map((item) => (
          <li key={item.issue_id} className="px-4 py-2 flex items-center gap-3" data-testid="paused-row">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-100 truncate">{item.title}</p>
              {item.identifier && <p className="text-[11px] font-mono text-ink-500 uppercase tracking-wider">{item.identifier}</p>}
              {errors[item.issue_id] && <p className="text-xs text-danger break-words">{errors[item.issue_id]}</p>}
            </div>
            <button
              type="button"
              onClick={() => void resume(item)}
              disabled={busy.has(item.issue_id)}
              data-testid="paused-resume"
              className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-md bg-info-soft ring-1 ring-info-line text-info hover:brightness-110 disabled:opacity-50"
            >
              {t('taskCenter.resume', 'Resume')}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default PausedSection;
