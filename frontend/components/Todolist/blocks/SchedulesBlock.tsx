/**
 * Context block: everything timed on this issue (harness 2b-2 §5-2) — the
 * one-shot wake-ups armed against it and the routine that created it.
 *
 * The card stays even with nothing on it: an issue with no wake-ups is
 * precisely when someone wants to arm one, so the "+ Later" entry point has
 * to be reachable there (it is the only place other than the composer). A
 * read that FAILED says so rather than showing an empty list — "no wake-ups"
 * and "I could not find out" are answers a person acts on differently.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, X } from 'lucide-react';

import { fmtWhen } from '../../../utils/fmtWhen';
import { listIssueSchedules, schedulesService, type IssueScheduleItem } from '../../../services/schedulesService';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { LaterPopover } from '../LaterPopover';
import { RailCard } from './StatusBlock';

export const SchedulesBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const issueId = Number(ctx.issue.id);
  const refreshKey = ctx.env.schedulesRefreshKey ?? 0;
  const [items, setItems] = useState<IssueScheduleItem[]>([]);
  // A CODE, not a sentence: `t` is a fresh function on every render, so
  // translating at store time would drag it into the effect's deps and make
  // the fetch re-run for ever (it did — the local cancel kept getting undone).
  const [error, setError] = useState<'load' | 'cancel' | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    listIssueSchedules(issueId)
      .then((rows) => {
        if (!live) return;
        setItems(rows);
        setError(null);
      })
      .catch((err) => {
        if (!live) return;
        console.error('[SchedulesBlock] load failed', err);
        setError('load');
      });
    return () => {
      live = false;
    };
  }, [issueId, refreshKey, reload]);

  const cancel = useCallback(
    async (id: string): Promise<void> => {
      setCancelling(id);
      setError(null);
      try {
        await schedulesService.remove(id);
        // Drop it locally rather than re-reading: the row is gone and the
        // list is short. A new copy, not a splice — nothing here mutates.
        setItems((rows) => rows.filter((r) => r.id !== id));
      } catch (err) {
        console.error('[SchedulesBlock] cancel failed', err);
        setError('cancel');
      } finally {
        setCancelling(null);
      }
    },
    [],
  );

  return (
    <RailCard title={t('schedule.title', 'Schedules')} testId="detail-schedules-panel">
      {items.map((row) => {
        const routine = !!row.cron_expr;
        return (
          <div key={row.id} data-testid="schedule-row" data-kind={routine ? 'routine' : 'once'} className="flex items-start gap-2 py-1 text-[12px]">
            <Clock size={11} className="mt-0.5 shrink-0 text-info" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-ink-300">{row.text}</div>
              <div className="text-[11px] text-ink-500">
                {routine ? row.cron_expr : fmtWhen(row.fire_at)}
                {' · '}
                {routine ? t('schedule.routine', 'Routine') : t('schedule.once', 'Once')}
                {!row.enabled && row.pause_reason ? ` · ${row.pause_reason}` : ''}
              </div>
            </div>
            <button
              type="button"
              data-testid="schedule-cancel"
              disabled={cancelling === row.id}
              onClick={() => void cancel(row.id)}
              title={t('schedule.cancel', 'Cancel')}
              className="shrink-0 rounded p-0.5 text-ink-500 hover:text-danger disabled:opacity-40"
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
      {items.length === 0 && !error && (
        <p data-testid="schedules-empty" className="py-1 text-[12px] text-ink-500">
          {t('schedule.empty', 'No Wake-ups Scheduled')}
        </p>
      )}
      {error && (
        <p data-testid="schedules-error" className="mt-1 break-words text-[11px] text-danger">
          {error === 'load'
            ? t('schedule.loadFailed', 'Could not read this issue’s schedules.')
            : t('schedule.cancelFailed', 'Could not cancel that wake-up.')}
        </p>
      )}
      <div className="relative mt-1">
        <button
          type="button"
          data-testid="schedules-add"
          onClick={() => setAddOpen((v) => !v)}
          className="text-[12px] text-info hover:brightness-110"
        >
          {t('schedule.add', '+ Later')}
        </button>
        {addOpen && (
          <LaterPopover
            issueId={issueId}
            text=""
            onClose={() => setAddOpen(false)}
            onScheduled={() => {
              setAddOpen(false);
              setReload((n) => n + 1);
            }}
          />
        )}
      </div>
    </RailCard>
  );
};

export const schedulesBlock: IssueBlock = {
  id: 'schedules',
  zone: 'context',
  // Between Budget (50) and Links (60): both are "facts about this issue",
  // and a wake-up is closer to spend than to navigation.
  order: 55,
  match: (ctx) => ctx.rollup !== null,
  component: SchedulesBlockView,
};
