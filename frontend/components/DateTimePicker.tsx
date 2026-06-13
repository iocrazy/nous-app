import React, { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface DateTimePickerProps {
  value: string; // ISO datetime string or empty
  onChange: (value: string) => void;
}

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

const QUICK_OPTIONS = [
  { label: '1 Hour', hours: 1 },
  { label: '24 Hours', hours: 24 },
  { label: '7 Days', hours: 168 },
  { label: '30 Days', hours: 720 },
];

function padZero(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function toLocalDatetime(date: Date): string {
  const y = date.getFullYear();
  const m = padZero(date.getMonth() + 1);
  const d = padZero(date.getDate());
  const h = padZero(date.getHours());
  const min = padZero(date.getMinutes());
  return `${y}-${m}-${d}T${h}:${min}`;
}

export const DateTimePicker: React.FC<DateTimePickerProps> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const now = useMemo(() => new Date(), []);
  const selected = value ? new Date(value) : null;

  const [viewYear, setViewYear] = useState(selected?.getFullYear() ?? now.getFullYear());
  const [viewMonth, setViewMonth] = useState(selected?.getMonth() ?? now.getMonth());
  const [hour, setHour] = useState(selected?.getHours() ?? now.getHours());
  const [minute, setMinute] = useState(selected?.getMinutes() ?? now.getMinutes());

  // Sync when value changes externally
  useEffect(() => {
    if (value) {
      const d = new Date(value);
      setViewYear(d.getFullYear());
      setViewMonth(d.getMonth());
      setHour(d.getHours());
      setMinute(d.getMinutes());
    }
  }, [value]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const calendarDays = useMemo(() => {
    const firstDay = new Date(viewYear, viewMonth, 1).getDay();
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const prevMonthDays = new Date(viewYear, viewMonth, 0).getDate();

    const days: { day: number; inMonth: boolean; date: Date }[] = [];

    // Previous month trailing
    for (let i = firstDay - 1; i >= 0; i--) {
      const d = prevMonthDays - i;
      days.push({ day: d, inMonth: false, date: new Date(viewYear, viewMonth - 1, d) });
    }
    // Current month
    for (let d = 1; d <= daysInMonth; d++) {
      days.push({ day: d, inMonth: true, date: new Date(viewYear, viewMonth, d) });
    }
    // Next month leading
    const remaining = 42 - days.length;
    for (let d = 1; d <= remaining; d++) {
      days.push({ day: d, inMonth: false, date: new Date(viewYear, viewMonth + 1, d) });
    }
    return days;
  }, [viewYear, viewMonth]);

  const commitDate = (date: Date, h: number, m: number) => {
    const result = new Date(date.getFullYear(), date.getMonth(), date.getDate(), h, m);
    onChange(toLocalDatetime(result));
  };

  const handleDayClick = (date: Date) => {
    commitDate(date, hour, minute);
  };

  const handleHourChange = (h: number) => {
    setHour(h);
    if (selected) commitDate(selected, h, minute);
  };

  const handleMinuteChange = (m: number) => {
    setMinute(m);
    if (selected) commitDate(selected, hour, m);
  };

  const handleQuick = (hours: number) => {
    const d = new Date(Date.now() + hours * 3600_000);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
    setHour(d.getHours());
    setMinute(d.getMinutes());
    onChange(toLocalDatetime(d));
  };

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1); }
    else setViewMonth(viewMonth - 1);
  };

  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1); }
    else setViewMonth(viewMonth + 1);
  };

  const isToday = (date: Date) => {
    return date.getDate() === now.getDate() &&
      date.getMonth() === now.getMonth() &&
      date.getFullYear() === now.getFullYear();
  };

  const isSelected = (date: Date) => {
    return selected &&
      date.getDate() === selected.getDate() &&
      date.getMonth() === selected.getMonth() &&
      date.getFullYear() === selected.getFullYear();
  };

  const displayValue = selected
    ? `${padZero(selected.getMonth() + 1)}/${padZero(selected.getDate())}/${selected.getFullYear()}, ${padZero(selected.getHours())}:${padZero(selected.getMinutes())}`
    : '';

  return (
    <div ref={ref} className="relative">
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full bg-ink-800 border border-ink-700 rounded-xl px-4 py-2.5 text-sm text-left transition-colors hover:border-ink-600 focus:outline-none focus:border-indigo-500/50"
      >
        <span className={displayValue ? 'text-ink-50' : 'text-ink-500'}>
          {displayValue || 'Select date & time...'}
        </span>
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute left-0 right-0 mt-2 bg-ink-900 border border-ink-700 rounded-2xl shadow-2xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Quick options */}
          <div className="flex gap-1.5 p-3 border-b border-ink-800">
            {QUICK_OPTIONS.map((opt) => (
              <button
                key={opt.label}
                type="button"
                onClick={() => handleQuick(opt.hours)}
                className="flex-1 px-2 py-1.5 text-xs font-medium text-ink-400 bg-ink-800 hover:bg-ink-700 hover:text-ink-200 rounded-lg transition-colors"
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="flex">
            {/* Calendar */}
            <div className="flex-1 p-3">
              {/* Month nav */}
              <div className="flex items-center justify-between mb-3">
                <button type="button" onClick={prevMonth} className="p-1 text-ink-400 hover:text-ink-50 transition-colors">
                  <ChevronLeft size={16} />
                </button>
                <span className="text-sm font-medium text-ink-200">
                  {MONTHS[viewMonth]} {viewYear}
                </span>
                <button type="button" onClick={nextMonth} className="p-1 text-ink-400 hover:text-ink-50 transition-colors">
                  <ChevronRight size={16} />
                </button>
              </div>

              {/* Weekday headers */}
              <div className="grid grid-cols-7 mb-1">
                {WEEKDAYS.map((d) => (
                  <div key={d} className="text-center text-[10px] text-ink-500 font-medium py-1">{d}</div>
                ))}
              </div>

              {/* Days */}
              <div className="grid grid-cols-7">
                {calendarDays.map(({ day, inMonth, date }, i) => (
                  <div key={i} className="flex items-center justify-center">
                    <button
                      type="button"
                      onClick={() => handleDayClick(date)}
                      className={`w-7 h-7 flex items-center justify-center rounded-full text-xs transition-colors ${
                        isSelected(date)
                          ? 'bg-indigo-600 text-white'
                          : isToday(date) && inMonth
                            ? 'text-indigo-400 font-semibold'
                            : inMonth
                              ? 'text-ink-300 hover:bg-ink-800'
                              : 'text-ink-600'
                      }`}
                    >
                      {day}
                    </button>
                  </div>
                ))}
              </div>

              {/* Clear / Today */}
              <div className="flex justify-between mt-2 px-1">
                <button
                  type="button"
                  onClick={() => { onChange(''); setOpen(false); }}
                  className="text-xs text-ink-500 hover:text-ink-300 transition-colors"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setViewYear(now.getFullYear());
                    setViewMonth(now.getMonth());
                    const d = new Date();
                    setHour(d.getHours());
                    setMinute(d.getMinutes());
                    onChange(toLocalDatetime(d));
                  }}
                  className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  Now
                </button>
              </div>
            </div>

            {/* Time picker */}
            <div className="w-24 border-l border-ink-800 flex">
              {/* Hours */}
              <div className="flex-1 overflow-y-auto max-h-[280px] scrollbar-thin">
                {Array.from({ length: 24 }, (_, h) => (
                  <button
                    key={h}
                    type="button"
                    onClick={() => handleHourChange(h)}
                    className={`w-full py-1.5 text-xs text-center transition-colors ${
                      h === hour
                        ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                        : 'text-ink-400 hover:bg-ink-800 hover:text-ink-200'
                    }`}
                  >
                    {padZero(h)}
                  </button>
                ))}
              </div>
              {/* Minutes */}
              <div className="flex-1 overflow-y-auto max-h-[280px] border-l border-ink-800/50 scrollbar-thin">
                {Array.from({ length: 12 }, (_, i) => i * 5).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => handleMinuteChange(m)}
                    className={`w-full py-1.5 text-xs text-center transition-colors ${
                      m === minute
                        ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                        : 'text-ink-400 hover:bg-ink-800 hover:text-ink-200'
                    }`}
                  >
                    {padZero(m)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DateTimePicker;
