// frontend/components/Inspiration/ActivityPanel.tsx
// One calendar for the whole page (spec §2.2 #2): heatmap ⇄ mini month,
// corner toggle persisted in localStorage; selected date is page state
// owned by the parent.
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, LayoutGrid } from 'lucide-react';
import { getActivity } from '../../services/inspirationService';

const MODE_KEY = 'inspiration.activityMode';
const WEEKS = 16;

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
  // Year/month quick picker (user ask: jump straight to a year+month instead
  // of paging one month at a time). pickerYear is the year being browsed
  // inside the picker; it re-anchors to the shown month each time it opens.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear());

  useEffect(() => {
    let alive = true;
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

  const pick = (day: string) => onSelectDate(day === selectedDate ? null : day);

  const setModePersist = (m: 'heatmap' | 'calendar') => {
    setMode(m);
    localStorage.setItem(MODE_KEY, m);
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
        <div>
          <div className="mb-1.5 flex items-center gap-1.5">
            <button
              aria-label="Choose year and month"
              onClick={() => {
                setPickerYear(month.getFullYear());
                setPickerOpen((v) => !v);
              }}
              className="rounded px-1 text-xs font-semibold tabular-nums text-content hover:bg-island-2 hover:text-indigo-300"
            >
              {month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
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
            <div className="mb-1.5 rounded-lg bg-island-2 p-2">
              <div className="mb-1.5 flex items-center justify-center gap-3">
                <button
                  aria-label="Previous year"
                  onClick={() => setPickerYear((y) => y - 1)}
                  className="rounded bg-island p-1 text-content-3"
                >
                  <ChevronLeft size={11} />
                </button>
                <span className="text-xs font-bold tabular-nums text-content">{pickerYear}</span>
                <button
                  aria-label="Next year"
                  onClick={() => setPickerYear((y) => y + 1)}
                  className="rounded bg-island p-1 text-content-3"
                >
                  <ChevronRight size={11} />
                </button>
              </div>
              <div className="grid grid-cols-4 gap-1">
                {Array.from({ length: 12 }, (_, i) => {
                  const isCurrent = pickerYear === month.getFullYear() && i === month.getMonth();
                  return (
                    <button
                      key={i}
                      onClick={() => {
                        setMonth(new Date(pickerYear, i, 1));
                        setPickerOpen(false);
                      }}
                      className={`rounded-md py-1 text-[10.5px] tabular-nums ${
                        isCurrent
                          ? 'bg-indigo-500 font-bold text-white'
                          : 'text-content-2 hover:bg-indigo-500/15 hover:text-indigo-300'
                      }`}
                    >
                      {new Date(2000, i, 1).toLocaleDateString('en-US', { month: 'short' })}
                    </button>
                  );
                })}
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
