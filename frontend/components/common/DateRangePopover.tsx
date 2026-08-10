/**
 * DateRangePopover — the shared calendar-range control for node scheduling
 * (spec: workspace IA redesign, task 8 / mock v4.2). Renders via a
 * `createPortal(document.body)` so it can float above any anchor regardless
 * of scroll containers, positioned with `position:fixed` off the anchor's
 * `getBoundingClientRect()`.
 *
 * Placement: below the anchor +6px by default; if the popover's *measured*
 * height (`offsetHeight`, not an estimated constant) would overflow the
 * viewport bottom, it flips above the anchor -6px instead. Re-measured on
 * every month navigation since jsdom/real browsers only know true height
 * after layout.
 *
 * Selection: first click sets `start`, second sets `end` (earlier date wins
 * `start` — clicking an earlier day than the pending start swaps them).
 * Ranges may span months; the visible month only changes via ‹ › nav, not
 * as a side effect of clicking a day in the (already visible) grid.
 *
 * Dates are `'YYYY-MM-DD'` strings throughout the public props — no `Date`
 * objects cross the component boundary. Internal Date math is fine since
 * ISO-8601 date strings sort lexically in calendar order, which several
 * comparisons below lean on directly.
 *
 * Replaces `frontend/components/workflow/NodeSchedulePicker.tsx` in tasks 9
 * and 10 — that component is left untouched here, only read for house
 * patterns (ISO parse/format, anchor-relative popovers).
 */

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface DateRangePopoverProps {
  anchorEl: HTMLElement | null; // null = closed
  start: string | null; // 'YYYY-MM-DD'
  end: string | null;
  onChange: (start: string | null, end: string | null) => void; // clear = (null, null)
  onClose: () => void;
}

export interface MonthCell {
  /** Local Date at noon (avoids DST/midnight edge flips). */
  date: Date;
  /** 'YYYY-MM-DD'. */
  iso: string;
  /** Whether this cell belongs to the month being rendered (vs a leading/trailing filler day). */
  inMonth: boolean;
}

const POPOVER_WIDTH = 260;

const WEEKDAY_KEYS = [
  ['common.dateRangePopover.weekdaySun', 'Su'],
  ['common.dateRangePopover.weekdayMon', 'Mo'],
  ['common.dateRangePopover.weekdayTue', 'Tu'],
  ['common.dateRangePopover.weekdayWed', 'We'],
  ['common.dateRangePopover.weekdayThu', 'Th'],
  ['common.dateRangePopover.weekdayFri', 'Fr'],
  ['common.dateRangePopover.weekdaySat', 'Sa'],
] as const;

/** 'YYYY-MM-DD' → local Date (noon). Returns null for null/invalid input. */
function parseISO(s: string | null): Date | null {
  if (!s) return null;
  const [y, m, d] = s.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d, 12);
}

function fmtISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/**
 * Pure 6-week (42-cell) grid for a given year + 0-based month (JS `Date`
 * convention: 0 = January). Always returns 42 cells so the popover height is
 * stable across months — leading/trailing cells from the adjacent months
 * fill out the first/last rows and carry `inMonth: false`.
 */
export function monthGrid(year: number, month: number): MonthCell[] {
  const first = new Date(year, month, 1, 12);
  const gridStart = new Date(year, month, 1 - first.getDay(), 12);
  const cells: MonthCell[] = [];
  for (let i = 0; i < 42; i++) {
    const date = new Date(
      gridStart.getFullYear(),
      gridStart.getMonth(),
      gridStart.getDate() + i,
      12,
    );
    cells.push({ date, iso: fmtISO(date), inMonth: date.getMonth() === month });
  }
  return cells;
}

export function DateRangePopover({
  anchorEl,
  start,
  end,
  onChange,
  onClose,
}: DateRangePopoverProps): React.ReactPortal | null {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const wasOpenRef = useRef(false);

  const [pendingStart, setPendingStart] = useState<string | null>(start);
  const [pendingEnd, setPendingEnd] = useState<string | null>(end);
  const [view, setView] = useState<{ year: number; month: number }>(() => {
    const base = parseISO(start) ?? new Date();
    return { year: base.getFullYear(), month: base.getMonth() };
  });

  // Resync from props whenever the popover transitions closed → open (fresh
  // open should reflect the caller's current committed range and the month
  // it derives from — not whatever selection was mid-flight last time).
  useEffect(() => {
    const isOpen = !!anchorEl;
    if (isOpen && !wasOpenRef.current) {
      setPendingStart(start);
      setPendingEnd(end);
      const base = parseISO(start) ?? new Date();
      setView({ year: base.getFullYear(), month: base.getMonth() });
    }
    wasOpenRef.current = isOpen;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorEl]);

  const place = useCallback(() => {
    if (!anchorEl || !ref.current) return;
    const r = anchorEl.getBoundingClientRect();
    const h = ref.current.offsetHeight;
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - 6 - h);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - POPOVER_WIDTH - 12));
    ref.current.style.top = `${top}px`;
    ref.current.style.left = `${left}px`;
  }, [anchorEl]);

  useLayoutEffect(place, [place, view.year, view.month]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [anchorEl, place]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current && !ref.current.contains(target) && !anchorEl.contains(target)) {
        onClose();
      }
    };
    const onScroll = () => onClose();
    document.addEventListener('keydown', onKeyDown);
    // Deferred so the click that opened the popover doesn't immediately close it.
    const timer = setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      clearTimeout(timer);
      document.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [anchorEl, onClose]);

  const gotoMonth = (delta: number) => {
    const total = view.year * 12 + view.month + delta;
    setView({ year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 });
  };

  const handleDayClick = (iso: string) => {
    if (!pendingStart || pendingEnd) {
      setPendingStart(iso);
      setPendingEnd(null);
      onChange(iso, null);
      return;
    }
    const s = iso < pendingStart ? iso : pendingStart;
    const e = iso < pendingStart ? pendingStart : iso;
    setPendingStart(s);
    setPendingEnd(e);
    onChange(s, e);
  };

  const handleClear = () => {
    setPendingStart(null);
    setPendingEnd(null);
    onChange(null, null);
  };

  if (!anchorEl) return null;

  const monthName = new Date(view.year, view.month, 1).toLocaleString('en-US', {
    month: 'long',
  });
  const monthYearLabel = t('common.dateRangePopover.monthYear', '{{monthName}} {{year}}', {
    monthName,
    monthNum: view.month + 1,
    year: view.year,
  });

  const cells = monthGrid(view.year, view.month);

  const dayClasses = (cell: MonthCell) => {
    const isStart = cell.iso === pendingStart;
    const isEnd = cell.iso === pendingEnd;
    const inRange =
      !!pendingStart && !!pendingEnd && cell.iso > pendingStart && cell.iso < pendingEnd;
    const base =
      'flex h-8 w-8 items-center justify-center rounded-md text-[13px] transition-colors motion-reduce:transition-none';
    if (isStart || isEnd) return `${base} bg-agent text-white`;
    if (inRange) return `${base} bg-agent-soft text-ink-100`;
    if (!cell.inMonth) return `${base} text-ink-600 hover:bg-line`;
    return `${base} text-ink-100 hover:bg-line`;
  };

  return createPortal(
    <div
      ref={ref}
      data-testid="date-range-popover"
      role="dialog"
      aria-label={t('common.dateRangePopover.title', 'Select date range')}
      className="z-[60] w-[260px] rounded-xl border border-line-strong bg-island p-3 text-ink-100 shadow-2xl"
      style={{ position: 'fixed', top: 0, left: 0 }}
    >
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          aria-label={t('common.dateRangePopover.prevMonth', 'Previous Month')}
          onClick={() => gotoMonth(-1)}
          className="flex h-6 w-6 items-center justify-center rounded text-ink-400 transition-colors hover:bg-line hover:text-ink-100 motion-reduce:transition-none"
        >
          <ChevronLeft size={14} />
        </button>
        <span className="text-[13px] font-medium text-ink-100">{monthYearLabel}</span>
        <button
          type="button"
          aria-label={t('common.dateRangePopover.nextMonth', 'Next Month')}
          onClick={() => gotoMonth(1)}
          className="flex h-6 w-6 items-center justify-center rounded text-ink-400 transition-colors hover:bg-line hover:text-ink-100 motion-reduce:transition-none"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      <div className="mb-1 grid grid-cols-7 gap-1">
        {WEEKDAY_KEYS.map(([key, def]) => (
          <span
            key={key}
            className="flex h-6 w-8 items-center justify-center text-[11px] text-ink-500"
          >
            {t(key, def)}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell) => (
          <button
            key={cell.iso}
            type="button"
            aria-label={cell.iso}
            onClick={() => handleDayClick(cell.iso)}
            className={dayClasses(cell)}
          >
            {cell.date.getDate()}
          </button>
        ))}
      </div>

      <div className="mt-2 flex items-center gap-2 border-t border-line pt-2 text-[12px]">
        <div
          data-testid="date-range-start-cell"
          className="flex-1 truncate rounded-md border border-line px-2 py-1 text-ink-200"
        >
          <span className="mr-1 text-ink-500">{t('common.dateRangePopover.start', 'Start')}</span>
          {pendingStart ?? t('common.dateRangePopover.empty', '–')}
        </div>
        <div
          data-testid="date-range-end-cell"
          className="flex-1 truncate rounded-md border border-line px-2 py-1 text-ink-200"
        >
          <span className="mr-1 text-ink-500">{t('common.dateRangePopover.end', 'End')}</span>
          {pendingEnd ?? t('common.dateRangePopover.empty', '–')}
        </div>
      </div>

      <div className="mt-2 flex justify-end">
        <button
          type="button"
          data-testid="date-range-clear"
          onClick={handleClear}
          className="rounded px-2 py-1 text-[12px] text-ink-500 transition-colors hover:text-ink-200 motion-reduce:transition-none"
        >
          {t('common.dateRangePopover.clear', 'Clear')}
        </button>
      </div>
    </div>,
    document.body,
  );
}

export default DateRangePopover;
