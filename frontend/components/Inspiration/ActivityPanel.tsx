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

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <div className="mb-2.5 flex items-center">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-content-3">
          {t('inspiration.activity', 'Activity')}
        </h3>
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
            <span className="text-xs font-semibold tabular-nums text-content">
              {month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
            </span>
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
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {['M', 'T', 'W', 'T2', 'F', 'S', 'S2'].map((d) => (
              <div key={d} className="pb-0.5 text-center text-[9px] font-semibold uppercase text-content-4">
                {d.charAt(0)}
              </div>
            ))}
            {monthCells.map(({ date, inMonth, dayNum }) => {
              const cnt = counts[date] ?? 0;
              const sel = selectedDate === date;
              return (
                <button
                  key={date}
                  aria-label={`${date}: ${cnt} notes`}
                  onClick={() => pick(date)}
                  className={`flex h-8 flex-col items-center justify-center gap-0.5 rounded-md text-[11px] tabular-nums ${
                    sel
                      ? 'bg-indigo-500 font-bold text-white'
                      : date === todayStr
                        ? 'font-bold text-indigo-300 shadow-[inset_0_0_0_1.5px] shadow-indigo-500'
                        : inMonth
                          ? 'text-content-2 hover:bg-island-2'
                          : 'text-content-4 opacity-50'
                  }`}
                >
                  {dayNum}
                  {cnt > 0 && <i className={`h-1 w-1 rounded-full ${sel ? 'bg-white/80' : heatClass(cnt)}`} />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
