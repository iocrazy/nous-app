/**
 * NodeSchedulePicker — the planned_start / planned_due date-range control for a
 * live workflow node (spec §5). A popover wrapping react-day-picker in range
 * mode: two months side by side, drag/click to select a span, a live "N days"
 * read-out, and Cancel / Clear / Done. Dates ride as 'YYYY-MM-DD' strings
 * (natural days, no timezone shift).
 *
 * Theming rides react-day-picker v10 CSS variables set on the wrapper, so the
 * calendar picks up the app accent in both light and dark.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import type { DateRange } from 'react-day-picker';
import { CalendarDays } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import 'react-day-picker/style.css';

interface NodeSchedulePickerProps {
  start: string | null;
  due: string | null;
  disabled?: boolean;
  onChange: (start: string | null, due: string | null) => void;
}

/** 'YYYY-MM-DD' → local Date (noon) so no day flips across timezones. */
function parseISO(s: string | null): Date | undefined {
  if (!s) return undefined;
  const [y, m, d] = s.split('-').map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d, 12);
}

function fmtISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function daySpan(range: DateRange | undefined): number {
  if (!range?.from || !range?.to) return 0;
  const ms = range.to.getTime() - range.from.getTime();
  return Math.round(ms / 86_400_000) + 1;
}

export const NodeSchedulePicker: React.FC<NodeSchedulePickerProps> = ({
  start,
  due,
  disabled,
  onChange,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange | undefined>();
  const rootRef = useRef<HTMLDivElement>(null);

  const committed = useMemo<DateRange | undefined>(() => {
    const from = parseISO(start);
    const to = parseISO(due);
    if (!from && !to) return undefined;
    return { from, to };
  }, [start, due]);

  useEffect(() => {
    if (!open) return;
    setDraft(committed);
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, committed]);

  const label = (() => {
    if (start && due) return `${start} → ${due}`;
    if (start) return `${start} → …`;
    return t('projects.workflow.setSchedule');
  })();

  const span = daySpan(draft);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        data-testid="workflow-schedule-trigger"
        className="flex h-8 w-full items-center gap-2 rounded-md border border-line px-2 text-[13px] text-ink-200 transition hover:border-line-strong disabled:opacity-50"
      >
        <CalendarDays size={14} className="shrink-0 text-ink-500" />
        <span className={`truncate ${start ? '' : 'text-ink-500'}`}>{label}</span>
      </button>

      {open && (
        <div
          data-testid="workflow-schedule-popover"
          className="absolute left-0 top-full z-40 mt-1 rounded-xl border border-line-strong bg-island p-3 shadow-2xl"
          style={
            {
              '--rdp-accent-color': 'var(--accent, #6366f1)',
              '--rdp-accent-background-color': 'var(--accent-soft, rgba(99,102,241,0.15))',
              '--rdp-day-height': '2rem',
              '--rdp-day-width': '2rem',
              '--rdp-font-family': 'inherit',
            } as React.CSSProperties
          }
        >
          <DayPicker
            mode="range"
            numberOfMonths={2}
            selected={draft}
            onSelect={setDraft}
            className="rdp-workflow text-ink-200"
          />
          <div className="mt-2 flex items-center justify-between border-t border-line pt-2">
            <span className="text-[12px] text-ink-400" data-testid="workflow-schedule-span">
              {span > 0 ? t('projects.workflow.days', { count: span }) : ''}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setDraft(undefined)}
                className="rounded px-2 py-1 text-[12px] text-ink-500 hover:text-ink-200"
              >
                {t('projects.workflow.dateRange.clear')}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded px-2 py-1 text-[12px] text-ink-400 hover:bg-ink-800 hover:text-ink-100"
              >
                {t('projects.workflow.dateRange.cancel')}
              </button>
              <button
                type="button"
                data-testid="workflow-schedule-done"
                onClick={() => {
                  onChange(
                    draft?.from ? fmtISO(draft.from) : null,
                    draft?.to ? fmtISO(draft.to) : null,
                  );
                  setOpen(false);
                }}
                className="rounded border px-2.5 py-1 text-[12px] transition"
                style={{
                  background: 'var(--accent-soft)',
                  color: 'var(--accent-text)',
                  borderColor: 'var(--accent-border)',
                }}
              >
                {t('projects.workflow.dateRange.done')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
