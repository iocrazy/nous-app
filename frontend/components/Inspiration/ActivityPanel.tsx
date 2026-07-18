// frontend/components/Inspiration/ActivityPanel.tsx
// One calendar for the whole page (spec §2.2 #2): heatmap ⇄ mini month,
// corner toggle persisted in localStorage; selected date is page state
// owned by the parent.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Calendar as CalendarIcon,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  LayoutGrid,
} from 'lucide-react';
import { getActivity } from '../../services/inspirationService';
import { monthsWithNotes, stepYear, visibleYearSlots } from './yearWheel';

const MODE_KEY = 'inspiration.activityMode';
const WEEKS = 16;
const WHEEL_THROTTLE_MS = 140;

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function heatClass(cnt: number): string {
  if (cnt <= 0) return 'bg-indigo-500/10';
  if (cnt === 1) return 'bg-indigo-500/25';
  if (cnt === 2) return 'bg-indigo-500/45';
  if (cnt <= 4) return 'bg-indigo-500/70';
  return 'bg-indigo-500';
}

// Soft background tint for month cells that carry notes — strong enough to
// scan at a glance (go-live feedback: the 4px dot was invisible), soft enough
// to keep the day number readable in both themes.
function cellTint(cnt: number): string {
  if (cnt <= 0) return '';
  if (cnt === 1) return 'bg-indigo-500/10';
  if (cnt <= 3) return 'bg-indigo-500/20';
  return 'bg-indigo-500/30';
}

interface Props {
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
  refreshKey: number;
}

export const ActivityPanel: React.FC<Props> = ({ selectedDate, onSelectDate, refreshKey }) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'heatmap' | 'calendar'>(
    () => (localStorage.getItem(MODE_KEY) === 'calendar' ? 'calendar' : 'heatmap'),
  );
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  // "Now" is stable for the lifetime of the panel — cheaper than re-reading the
  // clock every render and keeps the future-cap deterministic.
  const nowRef = useMemo(() => new Date(), []);
  const nowYear = nowRef.getFullYear();
  const nowMonth = nowRef.getMonth();

  // Year/month floating picker. `centerYear` is the year sitting in the middle
  // of the wheel; `focusMonth` is the keyboard-focused month cell (0-11).
  const [pickerOpen, setPickerOpen] = useState(false);
  const [centerYear, setCenterYear] = useState(nowYear);
  const [focusMonth, setFocusMonth] = useState(nowMonth);
  // Per-year set of month indices that carry notes, lazily fetched. Immutable
  // updates only. `requestedYears` guards against duplicate/looping fetches.
  const [yearMonths, setYearMonths] = useState<Record<number, Set<number>>>({});
  const requestedYears = useRef<Set<number>>(new Set());

  const popRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const wheelRef = useRef<HTMLDivElement | null>(null);
  const wheelLock = useRef(0);

  useEffect(() => {
    let alive = true;
    // A refresh means notes changed — drop the lazily-built year cache so the
    // picker re-fetches presence dots the next time it opens.
    requestedYears.current = new Set();
    setYearMonths({});
    (async () => {
      const to = new Date();
      const from = new Date(to.getTime() - (WEEKS * 7 - 1) * 86400000);
      try {
        const rows = await getActivity(ymd(from), ymd(to));
        if (!alive) return;
        const map: Record<string, number> = {};
        for (const r of rows) map[r.day] = r.cnt;
        setCounts(map);
      } catch (err) {
        console.error('activity load failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  // Lazily fetch full-year activity for the years currently visible in the
  // wheel (on open and whenever the center changes). Errors are treated as an
  // empty year and not retried.
  useEffect(() => {
    if (!pickerOpen) return;
    for (const y of visibleYearSlots(centerYear, nowYear)) {
      if (y == null || requestedYears.current.has(y)) continue;
      requestedYears.current.add(y);
      getActivity(`${y}-01-01`, `${y}-12-31`)
        .then((rows) => setYearMonths((prev) => ({ ...prev, [y]: monthsWithNotes(rows) })))
        .catch((err) => {
          console.error('activity year load failed', err);
          setYearMonths((prev) => ({ ...prev, [y]: new Set<number>() }));
        });
    }
  }, [pickerOpen, centerYear, nowYear]);

  // Outside-click + Escape close the popover.
  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (popRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setPickerOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPickerOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [pickerOpen]);

  // Mouse wheel over the rail changes the year. Attached natively (non-passive)
  // so preventDefault can stop the page from scrolling. Throttled to one step.
  useEffect(() => {
    const el = wheelRef.current;
    if (!pickerOpen || !el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const now = Date.now();
      if (now - wheelLock.current < WHEEL_THROTTLE_MS) return;
      wheelLock.current = now;
      const dir = e.deltaY > 0 ? 1 : -1;
      setCenterYear((y) => stepYear(y, dir, nowYear));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [pickerOpen, nowYear]);

  // Focus the popover so ArrowUp/Down/Left/Right/Enter reach its key handler.
  useEffect(() => {
    if (pickerOpen) popRef.current?.focus();
  }, [pickerOpen]);

  const pick = (day: string) => onSelectDate(day === selectedDate ? null : day);

  const setModePersist = (m: 'heatmap' | 'calendar') => {
    setMode(m);
    localStorage.setItem(MODE_KEY, m);
  };

  const openPicker = () => {
    if (pickerOpen) {
      setPickerOpen(false);
      return;
    }
    setCenterYear(month.getFullYear());
    setFocusMonth(month.getMonth());
    setPickerOpen(true);
  };

  const commitMonth = (year: number, monthIdx: number) => {
    setMonth(new Date(year, monthIdx, 1));
    setPickerOpen(false);
  };

  const backToThisMonth = () => {
    setMonth(new Date(nowYear, nowMonth, 1));
    setCenterYear(nowYear);
    setFocusMonth(nowMonth);
    setPickerOpen(false);
  };

  const onPickerKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowUp':
        e.preventDefault();
        setCenterYear((y) => stepYear(y, -1, nowYear));
        break;
      case 'ArrowDown':
        e.preventDefault();
        setCenterYear((y) => stepYear(y, 1, nowYear));
        break;
      case 'ArrowLeft':
        e.preventDefault();
        setFocusMonth((m) => Math.max(0, m - 1));
        break;
      case 'ArrowRight':
        e.preventDefault();
        setFocusMonth((m) => Math.min(11, m + 1));
        break;
      case 'Enter':
        e.preventDefault();
        if (!(centerYear === nowYear && focusMonth > nowMonth)) commitMonth(centerYear, focusMonth);
        break;
      case 'Escape':
        e.preventDefault();
        setPickerOpen(false);
        break;
      default:
        break;
    }
  };

  // 16 weeks of columns, Monday-first, ending today.
  const heatDays = useMemo(() => {
    const today = new Date();
    const dow = (today.getDay() + 6) % 7; // Mon=0
    const end = new Date(today.getTime() + (6 - dow) * 86400000);
    const days: string[] = [];
    for (let i = WEEKS * 7 - 1; i >= 0; i--) days.push(ymd(new Date(end.getTime() - i * 86400000)));
    return days;
  }, []);

  const monthCells = useMemo(() => {
    const first = new Date(month);
    const lead = (first.getDay() + 6) % 7;
    const start = new Date(first.getTime() - lead * 86400000);
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(start.getTime() + i * 86400000);
      return { date: ymd(d), inMonth: d.getMonth() === month.getMonth(), dayNum: d.getDate() };
    });
  }, [month]);

  const todayStr = ymd(new Date());

  // Notes in the displayed month (footer stat, go-live feedback: "richer").
  const monthTotal = useMemo(() => {
    const prefix = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}-`;
    return Object.entries(counts).reduce(
      (sum, [day, cnt]) => (day.startsWith(prefix) ? sum + cnt : sum),
      0,
    );
  }, [counts, month]);

  const goToday = () => {
    const now = new Date();
    setMonth(new Date(now.getFullYear(), now.getMonth(), 1));
    setPickerOpen(false);
    onSelectDate(todayStr);
  };

  const centerNotes = yearMonths[centerYear];

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <div className="mb-2.5 flex items-center">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-content-3">
          {t('inspiration.activity', 'Activity')}
        </h3>
        {selectedDate && (
          <button
            aria-label="Clear selected day"
            onClick={() => onSelectDate(null)}
            className="ml-2 inline-flex items-center gap-1 rounded-full bg-indigo-500/15 px-2 py-0.5 text-[10px] tabular-nums text-indigo-300 hover:bg-indigo-500/25"
          >
            {selectedDate} ×
          </button>
        )}
        <div className="ml-auto flex rounded-md bg-island-2 p-0.5">
          <button
            aria-label="Heatmap view"
            onClick={() => setModePersist('heatmap')}
            className={`rounded px-1.5 py-0.5 ${mode === 'heatmap' ? 'bg-island text-indigo-300' : 'text-content-4'}`}
          >
            <LayoutGrid size={11} />
          </button>
          <button
            aria-label="Calendar view"
            onClick={() => setModePersist('calendar')}
            className={`rounded px-1.5 py-0.5 ${mode === 'calendar' ? 'bg-island text-indigo-300' : 'text-content-4'}`}
          >
            <CalendarIcon size={11} />
          </button>
        </div>
      </div>

      {mode === 'heatmap' ? (
        <div className="grid grid-flow-col grid-rows-7 gap-[3px]">
          {heatDays.map((day) => {
            const cnt = counts[day] ?? 0;
            return (
              <button
                key={day}
                aria-label={`${day}: ${cnt} notes`}
                onClick={() => pick(day)}
                className={`aspect-square w-full rounded-[3px] ${heatClass(cnt)} ${
                  selectedDate === day ? 'ring-1 ring-content ring-offset-1 ring-offset-island' : ''
                }`}
              />
            );
          })}
        </div>
      ) : (
        <div className="relative">
          <div className="mb-1.5 flex items-center gap-1.5">
            <button
              ref={triggerRef}
              aria-label="Choose year and month"
              aria-expanded={pickerOpen}
              onClick={openPicker}
              className="inline-flex items-center gap-1 rounded px-1 text-xs font-semibold tabular-nums text-content hover:bg-island-2 hover:text-indigo-300"
            >
              {month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
              <ChevronDown
                size={11}
                className={`text-content-4 transition-transform ${pickerOpen ? 'rotate-180' : ''}`}
              />
            </button>
            <button
              aria-label="Previous month"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
              className="ml-auto rounded bg-island-2 p-1 text-content-3"
            >
              <ChevronLeft size={11} />
            </button>
            <button
              aria-label="Next month"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
              className="rounded bg-island-2 p-1 text-content-3"
            >
              <ChevronRight size={11} />
            </button>
            <button
              onClick={goToday}
              className="rounded bg-island-2 px-1.5 py-0.5 text-[10px] font-semibold text-content-3 hover:text-indigo-300"
            >
              {t('inspiration.today', 'Today')}
            </button>
          </div>

          {pickerOpen && (
            <div
              ref={popRef}
              role="dialog"
              aria-label="Year and month picker"
              tabIndex={-1}
              onKeyDown={onPickerKeyDown}
              className="mh-year-pop absolute left-0 right-0 top-8 z-20 rounded-2xl border border-line-strong bg-island p-3.5 shadow-[0_12px_32px_rgba(0,0,0,.4),0_2px_8px_rgba(0,0,0,.3)] outline-none"
            >
              <div className="flex gap-3">
                <div
                  ref={wheelRef}
                  className="flex w-16 flex-col justify-center border-r border-line pr-2.5"
                >
                  {visibleYearSlots(centerYear, nowYear).map((y, idx) => {
                    const dist = Math.abs(idx - 2);
                    return (
                      <div key={idx} className="flex h-[30px] items-center justify-center">
                        {y != null && (
                          <button
                            aria-label={`Year ${y}`}
                            aria-current={dist === 0 ? 'true' : undefined}
                            onClick={() => setCenterYear(y)}
                            className={`relative rounded-[9px] px-2.5 py-[3px] leading-none tabular-nums transition-[color,opacity,transform,background,font-size] duration-[180ms] ${
                              dist === 0
                                ? 'bg-[var(--accent-soft)] text-[13.5px] font-extrabold text-[var(--accent-text)] shadow-[inset_0_0_0_1px_var(--accent-border)]'
                                : dist === 1
                                  ? 'text-xs font-semibold text-content-3 hover:bg-island-2 hover:text-[var(--accent-text)]'
                                  : 'text-[11px] font-semibold text-content-4 opacity-55 hover:bg-island-2 hover:text-[var(--accent-text)] hover:opacity-100'
                            }`}
                          >
                            {y}
                            {yearMonths[y]?.size ? (
                              <span className="absolute right-0.5 top-1/2 h-[3.5px] w-[3.5px] -translate-y-1/2 rounded-full bg-accent opacity-75" />
                            ) : null}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div className="grid flex-1 grid-cols-3 content-center gap-1.5">
                  {Array.from({ length: 12 }, (_, i) => {
                    const isSel = centerYear === month.getFullYear() && i === month.getMonth();
                    const isNow = centerYear === nowYear && i === nowMonth;
                    const isFuture = centerYear === nowYear && i > nowMonth;
                    const isFocus = i === focusMonth;
                    const hasNotes = centerNotes?.has(i) ?? false;
                    return (
                      <button
                        key={i}
                        aria-label={`${new Date(2000, i, 1).toLocaleDateString('en-US', { month: 'long' })} ${centerYear}`}
                        aria-current={isSel ? 'true' : undefined}
                        disabled={isFuture}
                        onClick={() => commitMonth(centerYear, i)}
                        className={`relative rounded-[10px] border px-0 pb-[13px] pt-[9px] text-xs font-semibold tabular-nums transition-colors ${
                          isSel
                            ? 'border-transparent bg-accent font-extrabold text-white shadow-[0_2px_8px_rgba(99,102,241,.35)]'
                            : isFuture
                              ? 'cursor-default border-transparent text-content-4 opacity-55'
                              : `border-transparent text-content-2 hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)] hover:text-[var(--accent-text)] ${
                                  isFocus ? 'ring-1 ring-[var(--accent-border)]' : ''
                                }`
                        }`}
                      >
                        {new Date(2000, i, 1).toLocaleDateString('en-US', { month: 'short' })}
                        {hasNotes ? (
                          <span
                            className={`absolute bottom-[5px] left-1/2 h-1 w-1 -translate-x-1/2 rounded-full ${
                              isSel ? 'bg-white' : 'bg-accent'
                            } opacity-85`}
                          />
                        ) : isNow && !isSel ? (
                          <span className="absolute bottom-[5px] left-1/2 h-[3.5px] w-[3.5px] -translate-x-1/2 rounded-full border border-[var(--accent-text)]" />
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="mt-3 flex items-center border-t border-line pt-2.5">
                <span className="inline-flex items-center gap-1.5 text-[10.5px] text-content-4">
                  <i className="h-1 w-1 rounded-full bg-accent" />
                  {t('inspiration.hasNotes', 'has notes')}
                </span>
                <button
                  onClick={backToThisMonth}
                  className="ml-auto rounded-md bg-island-2 px-2.5 py-1 text-[11px] font-semibold text-content-2 hover:text-[var(--accent-text)]"
                >
                  {t('inspiration.backToThisMonth', 'Back to this month')}
                </button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-7 gap-1">
            {['M', 'T', 'W', 'T2', 'F', 'S', 'S2'].map((d) => (
              <div key={d} className="pb-0.5 text-center text-[9px] font-semibold uppercase text-content-4">
                {d.charAt(0)}
              </div>
            ))}
            {monthCells.map(({ date, inMonth, dayNum }) => {
              const cnt = counts[date] ?? 0;
              const sel = selectedDate === date;
              const isToday = date === todayStr;
              return (
                <button
                  key={date}
                  aria-label={`${date}: ${cnt} notes`}
                  title={cnt > 0 ? t('inspiration.dayNotes', '{{count}} notes', { count: cnt }) : undefined}
                  onClick={() => pick(date)}
                  className={`flex h-10 flex-col items-center justify-center rounded-lg text-[11.5px] tabular-nums transition-colors ${
                    sel
                      ? 'bg-indigo-500 font-bold text-white'
                      : `${cellTint(cnt)} ${
                          isToday
                            ? 'font-bold text-indigo-300 shadow-[inset_0_0_0_1.5px] shadow-indigo-500'
                            : inMonth
                              ? 'text-content-2'
                              : 'text-content-4 opacity-50'
                        } hover:bg-indigo-500/15 hover:shadow-[inset_0_0_0_1px] hover:shadow-indigo-500/40`
                  }`}
                >
                  <span className="leading-none">{dayNum}</span>
                  {cnt > 0 && (
                    <span
                      className={`mt-0.5 rounded-full px-1 text-[9px] font-bold leading-[13px] ${
                        sel ? 'bg-white/25 text-white' : 'bg-indigo-500/80 text-white'
                      }`}
                    >
                      {cnt}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-2 flex items-center justify-between border-t border-line pt-2 text-[10.5px] text-content-4 tabular-nums">
            <span>{t('inspiration.monthNotes', '{{count}} notes this month', { count: monthTotal })}</span>
            <span className="inline-flex items-center gap-1">
              <i className="h-2 w-2 rounded-sm bg-indigo-500/30" />
              {t('inspiration.hasNotes', 'has notes')}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
